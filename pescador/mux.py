'''Stream multiplexing

Defines the interface and several varieties of "Mux". A "Mux" is
is a Streamer which wraps N other streamers, and at every step yields a
sample from one of its sub-streamers.

This module defines the following Mux types:

`PoissonMux`

    A Mux which chooses it's active streams stochastically, and chooses
    samples from the active streams stochastically. `PoissonMux` is equivalent
    to the `pescador.Mux` from versions <2.0.

     `PoissonMux` has a `mode` parameter which selects how it operates, with
     the following modes:

    `with_replacement`

        Sample streamers with replacement.  This allows a single stream to
        be used multiple times (even simultaneously).

    `exhaustive`

        Each streamer is consumed at most once and never
        revisited.

    `single_active`

        Once a stream from the candidate pool is activated,
        it can be used at most once. Streams are revived when
        they are exhausted. This setting makes it so that streams in the
        active pool are uniquely selected from the candidate pool.

`ShuffledMux`

    A `ShuffledMux` uses all the given streamers, and samples from
    each of them with equal probability.

`RoundRobinMux`

    As in `ShuffledMux`, uses all the given streamers, but iterates over
    the streamers in strict order.

`ChainMux`

    As in itertools.chain(), runs the first streamer to exhaustion, then
    the second, then the third, etc. Uses only a single stream at a time.

`Mux`

    The pescador<2.0 `Mux` is still available and works the same,
    but is deprecated.
'''
import six
import numpy as np

from . import core
from .exceptions import PescadorError
from .util import Deprecated, rename_kw


class Mux(core.Streamer):
    '''Stochastic multiplexor for Streamers

    Examples
    --------
    >>> # Create a collection of streamers
    >>> seeds = [pescador.Streamer(my_generator) for i in range(10)]
    >>> # Multiplex them together into a single streamer
    >>> # Use at most 3 streams at once
    >>> mux = pescador.Mux(seeds, k=3)
    >>> for batch in mux():
    ...     MY_FUNCTION(batch)

    Mux([stream, range(8), stream2])
    '''

    def __init__(self, streamers, k,
                 rate=256.0, weights=None, with_replacement=True,
                 prune_empty_streams=True, revive=False,
                 random_state=None,
                 seed_pool=Deprecated(),
                 lam=Deprecated(),
                 pool_weights=Deprecated(),
                 prune_empty_seeds=Deprecated()):
        """Given an array (pool) of streamer types, do the following:

        1. Select ``k`` streams at random to iterate from
        2. Assign each activated stream a sample count ~ Poisson(lam)
        3. Yield samples from the streams by randomly multiplexing
           from the active set.
        4. When a stream is exhausted, select a new one from `streamers`.

        Parameters
        ----------
        streamers : iterable of streamers
            The collection of streamer-type objects

        k : int > 0
            The number of streams to keep active at any time.

        rate : float > 0 or None
            Rate parameter for the Poisson distribution governing sample counts
            for individual streams.
            If ``None``, sample infinitely from each stream.

        weights : np.ndarray or None
            Optional weighting for ``streamers``.
            If ``None``, then weights are assumed to be uniform.
            Otherwise, ``weights[i]`` defines the sampling proportion
            of ``streamers[i]``.

            Must have the same length as ``streamers``.

        with_replacement : bool
            Sample streamers with replacement.  This allows a single stream to
            be used multiple times (even simultaneously).
            If ``False``, then each streamer is consumed at most once and never
            revisited.

        prune_empty_streams : bool
            Disable streamers that produce no data.
            If ``True``, streamers that previously produced no data are never
            revisited.
            Note:
            1. This may be undesireable for streams where past emptiness
            may not imply future emptiness.
            2. Failure to prune truly empty streams with `revive=True` can
            result in infinite looping behavior. Disable with caution.

        revive: bool
            If ``with_replacement`` is ``False``, setting ``revive=True``
            will re-insert previously exhausted streams into the candidate set.

            This configuration allows a stream to be active at most once at any
            time.

        random_state : None, int, or np.random.RandomState
            If int, random_state is the seed used by the random number
            generator;

            If RandomState instance, random_state is the random number
            generator;

            If None, the random number generator is the RandomState instance
            used by np.random.

        seed_pool : iterable of streamers
            .. warning:: This parameter name was deprecated in pescador 1.1
                Use the `streamers` parameter instead.
                The `seed_pool` parameter will be removed in pescador 2.0.

        lam : float > 0.0
            .. warning:: This parameter name was deprecated in pescador 1.1
                Use the `rate` parameter instead.
                The `lam` parameter will be removed in pescador 2.0.

        pool_weights : np.ndarray or None
            .. warning:: This parameter name was deprecated in pescador 1.1
                Use the `weights` parameter instead.
                The `pool_weights` parameter will be removed in pescador 2.0.

        prune_empty_seeds : bool
            .. warning:: This parameter name was deprecated in pescador 1.1
                Use the `prune_empty_streams` parameter instead.
                The `prune_empty_seeds` parameter will be removed in
                pescador 2.0.
        """
        streamers = rename_kw('seed_pool', seed_pool,
                              'streamers', streamers,
                              '1.1', '2.0')
        rate = rename_kw('lam', lam,
                         'rate', rate,
                         '1.1', '2.0')
        weights = rename_kw('pool_weights', pool_weights,
                            'weights', weights,
                            '1.1', '2.0')
        prune_empty_streams = rename_kw(
            'prune_empty_seeds', prune_empty_seeds,
            'prune_empty_streams', prune_empty_streams,
            '1.1', '2.0')
        self.streamers = streamers
        self.n_streams = len(streamers)
        self.k = k
        self.rate = rate
        self.weights = weights
        self.with_replacement = with_replacement
        self.prune_empty_streams = prune_empty_streams
        self.revive = revive

        self.deactivate()

        if random_state is None:
            self.rng = np.random
        elif isinstance(random_state, int):
            self.rng = np.random.RandomState(seed=random_state)
        elif isinstance(random_state, np.random.RandomState):
            self.rng = random_state
        else:
            raise PescadorError('Invalid random_state={}'.format(random_state))

        if not self.n_streams:
            raise PescadorError('Cannot mux an empty collection')

        if self.weights is None:
            self.weights = 1. / self.n_streams * np.ones(self.n_streams)

        self.weights = np.atleast_1d(self.weights)

        if len(self.weights) != len(self.streamers):
            raise PescadorError('`weights` must be the same '
                                'length as `streamers`')

        if not (self.weights > 0.0).any():
            raise PescadorError('`weights` must contain at least '
                                'one positive value')

        self.weights /= np.sum(self.weights)

    def activate(self):
        """Activates a number of streams"""
        self.distribution_ = 1. / self.n_streams * np.ones(self.n_streams)
        self.valid_streams_ = np.ones(self.n_streams, dtype=bool)

        self.streams_ = [None] * self.k

        self.stream_weights_ = np.zeros(self.k)
        self.stream_counts_ = np.zeros(self.k, dtype=int)
        # Array of pointers into `self.streamers`
        self.stream_idxs_ = np.zeros(self.k, dtype=int)

        for idx in range(self.k):

            if not (self.distribution_ > 0).any():
                break

            self.stream_idxs_[idx] = self.rng.choice(
                self.n_streams, p=self.distribution_)
            self.streams_[idx], self.stream_weights_[idx] = (
                self._new_stream(self.stream_idxs_[idx]))

        self.weight_norm_ = np.sum(self.stream_weights_)

    def deactivate(self):
        self.streams_ = None
        self.stream_weights_ = None
        self.stream_counts_ = None
        self.stream_idxs_ = None
        self.weight_norm_ = None

    def iterate(self, max_iter=None):
        with core.StreamActivator(self):

            # Main sampling loop
            n = 0

            if max_iter is None:
                max_iter = np.inf

            while n < max_iter and self.weight_norm_ > 0.0:
                # Pick a stream from the active set
                idx = self.rng.choice(self.k, p=(self.stream_weights_ /
                                                 self.weight_norm_))

                # Can we sample from it?
                try:
                    # Then yield the sample
                    yield six.advance_iterator(self.streams_[idx])

                    # Increment the sample counter
                    n += 1
                    self.stream_counts_[idx] += 1

                except StopIteration:
                    # Oops, this one's exhausted.

                    if (self.prune_empty_streams and
                            self.stream_counts_[idx] == 0):
                        # If we're disabling empty seeds, see if this stream
                        # produced data; if it didn't, turn it off.
                        self.distribution_[self.stream_idxs_[idx]] = 0.0
                        self.valid_streams_[self.stream_idxs_[idx]] = False

                    if self.revive and not self.with_replacement:
                        # When this case is hit, the `distribution_` for
                        # this "seed"/"stream" is 0.0, because it got set
                        # to when we activated it. (in `_new_stream`)

                        # Since revive mode is on, we set it to the max
                        # current probability to enable it to be used again.
                        if self.distribution_.any():
                            self.distribution_[self.stream_idxs_[idx]] = (
                                np.max(self.distribution_))
                        else:
                            self.distribution_[self.stream_idxs_[idx]] = 1.0

                    if (self.distribution_ > 0).any():
                        # Replace it and move on if there are still seeds
                        # in the pool.
                        self.distribution_[:] /= np.sum(self.distribution_)

                        self.stream_idxs_[idx] = self.rng.choice(
                            self.n_streams, p=self.distribution_)

                        self.streams_[idx], self.stream_weights_[idx] = (
                            self._new_stream(self.stream_idxs_[idx]))

                        self.stream_counts_[idx] = 0

                    else:
                        # Otherwise, this one's exhausted.
                        # Set its probability to 0
                        self.stream_weights_[idx] = 0.0

                    self.weight_norm_ = np.sum(self.stream_weights_)

                # If everything has been pruned, kill the while loop
                if not self.valid_streams_.any():
                    break

    def _new_stream(self, idx):
        '''Randomly select and create a stream.

        Parameters
        ----------
        idx : int, [0:n_streams - 1]
            The stream index to replace
        '''
        if len(self.streamers) != len(self.weights):
            raise PescadorError('`streamers` must have the same '
                                'length as `weights`')

        if len(self.streamers) != len(self.distribution_):
            raise PescadorError('`streamers` must have the same '
                                'length as `distribution`')

        # instantiate
        if self.rate is not None:
            n_stream = 1 + self.rng.poisson(lam=self.rate)
        else:
            n_stream = None

        # If we're sampling without replacement, zero this one out
        # This effectively disables this stream as soon as it is chosen,
        # preventing it from being chosen again (unless it is revived)
        if not self.with_replacement:
            self.distribution_[idx] = 0.0

            # Correct the distribution
            if (self.distribution_ > 0).any():
                self.distribution_[:] /= np.sum(self.distribution_)

        return (self.streamers[idx].iterate(max_iter=n_stream),
                self.weights[idx])


class BaseMux(core.Streamer):
    """BaseMux defines the interface to a Mux. Fundamentally, a Mux
    is a container for multiple Streamers, which selects a Sample from one of
    its streamers at every iteration.

    A Mux has the following fundamental behaviors:

     * When "activated", choose a subset of available streamers to stream from
       (the "active substreams")
     * When a sample is drawn from the mux (via generate),
       chooses which active substream to stream from.
     * Handles exhaustion of streams (restarting, replacing, ...)

    """
    def __init__(self, streamers, k, weights=None, random_state=None,
                 prune_empty_streams=True):
        """
        Parameters
        ----------
        streamers : iterable of streamers
            The collection of streamer-type objects

        k : int > 0
            The number of streams to keep active at any time.

        weights : np.ndarray or None
            Optional weighting for ``streamers``.
            If ``None``, then weights are assumed to be uniform.
            Otherwise, ``weights[i]`` defines the sampling proportion
            of ``streamers[i]``.

            Must have the same length as ``streamers``.

        random_state : None, int, or np.random.RandomState
            If int, random_state is the seed used by the random number
            generator;

            If RandomState instance, random_state is the random number
            generator;

            If None, the random number generator is the RandomState instance
            used by np.random.

        prune_empty_streams : bool
            Disable streamers that produce no data. If ``True``,
            streamers that previously produced no data are never
            revisited.
            Note:
            1. This may be undesireable for streams where past emptiness
            may not imply future emptiness.
            2. [TODO: UPDATE] Failure to prune truly empty streams with `revive=True` can result in infinite looping behavior. Disable with caution.
        """
        self.streamers = streamers
        self.n_streams = len(streamers)
        self.k = k
        self.prune_empty_streams = prune_empty_streams
        self.weights = weights

        # Clear state and reset actiave/deactivate params.
        self.deactivate()

        if random_state is None:
            self.rng = np.random
        elif isinstance(random_state, int):
            self.rng = np.random.RandomState(seed=random_state)
        elif isinstance(random_state, np.random.RandomState):
            self.rng = random_state
        else:
            raise PescadorError('Invalid random_state={}'.format(random_state))

        if not self.n_streams:
            raise PescadorError('Cannot mux an empty collection')

        if self.weights is None:
            self.weights = 1. / self.n_streams * np.ones(self.n_streams)
        self.weights = np.atleast_1d(self.weights)

        if len(self.weights) != len(self.streamers):
            raise PescadorError('`weights` must be the same '
                                'length as `streamers`')

        if not (self.weights > 0.0).any():
            raise PescadorError('`weights` must contain at least '
                                'one positive value')

        self.weights /= np.sum(self.weights)

    def activate(self):
        """Activates the mux as a streamer, choosing which substreams to
        select as active."""
        # These do not depend on the number of streams, k
        # This function must be filled out in more detail in a child Mux.
        self.distribution_ = 1. / self.n_streams * np.ones(self.n_streams)
        self.valid_streams_ = np.ones(self.n_streams, dtype=bool)

        # The active streamers
        self.streams_ = [None] * self.k

        # Weights of the active streams.
        # Once a stream is exhausted, it is set to 0
        self.stream_weights_ = np.zeros(self.k)
        # How many samples have been draw from each (active) stream.
        self.stream_counts_ = np.zeros(self.k, dtype=int)
        # Array of pointers into `self.streamers`
        self.stream_idxs_ = np.zeros(self.k, dtype=int)

        # Initialize each active stream.
        for idx in range(self.k):

            if not (self.distribution_ > 0).any():
                break

            # Setup a new streamer at this index.
            self._new_stream(idx)

        self.weight_norm_ = np.sum(self.stream_weights_)

    def deactivate(self):
        """Reset the Mux state."""
        self.streams_ = None
        self.stream_idxs_ = None
        self.stream_counts_ = None
        self.stream_weights_ = None
        self.weight_norm_ = None

    def iterate(self, max_iter=None):
        """Yields items from the mux."""
        if max_iter is None:
            max_iter = np.inf

        with core.StreamActivator(self):
            # Main sampling loop
            n = 0

            while n < max_iter and self.weight_norm_ > 0.0:
                # Pick a stream from the active set
                idx = self._next_sample_index()

                # Can we sample from it?
                try:
                    # Then yield the sample
                    yield six.advance_iterator(self.streams_[idx])

                    # Increment the sample counter
                    n += 1
                    self.stream_counts_[idx] += 1

                except StopIteration:
                    # Oops, this stream is exhausted.

                    # If we're disabling empty seeds, see if this stream
                    # produced data at any point; if it didn't, turn it off.
                    #  (Note) prune_empty_streams applies to all Muxes?
                    if (self.prune_empty_streams and
                            self.stream_counts_[idx] == 0):
                        self.distribution_[self.stream_idxs_[idx]] = 0.0
                        self.valid_streams_[self.stream_idxs_[idx]] = False

                    # Call child-class exhausted-stream behavior
                    self._on_stream_exhausted(idx)

                    # If there are active streams reamining,
                    # choose a new one to make active.
                    if (self.distribution_ > 0).any():
                        # Replace it and move on if there are still seeds
                        # in the pool.
                        self.distribution_[:] /= np.sum(self.distribution_)

                        # Setup a new streamer at this index.
                        self._new_stream(idx)
                    else:
                        # Otherwise, this one's exhausted.
                        # Set its probability to 0
                        self.stream_weights_[idx] = 0.0

                    self.weight_norm_ = np.sum(self.stream_weights_)

                # If everything has been pruned, kill the while loop
                if not self.valid_streams_.any():
                    break

    def _on_stream_exhausted(self, idx):
        """Override this to provide a Mux with additional behavior
        when a stream is exhausted. This gets called *after* streams
        are pruned.

        Parameters
        ----------
        idx : int, [0:k - 1]
            Index of the exhausted stream (in `self.stream_idxs_`).
        """
        pass

    def _n_samples_to_stream(self):
        """Return how many samples to stream for a new streamer. None
        makes an infinite streamer. If the `BaseMux` subclass has a
        `rate` field, it would be returned here. The default - None -
        makes the resulting streamers infinite. (`max_iter`=None)
        """
        return None

    def _activate_stream(self, idx):
        '''Randomly select and create a stream.

        Parameters
        ----------
        idx : int, [0:n_streams - 1]
            The stream index to replace
        '''
        if len(self.streamers) != len(self.weights):
            raise PescadorError('`streamers` must have the same '
                                'length as `weights`')

        if len(self.streamers) != len(self.distribution_):
            raise PescadorError('`streamers` must have the same '
                                'length as `distribution`')

        # Get the number of samples for this streamer.
        n_stream = self._n_samples_to_stream()

        # instantiate a new streamer
        return (self.streamers[idx].iterate(max_iter=n_stream),
                self.weights[idx])

    def _new_stream_index(self, idx=None):
        """Returns an index of a streamer from `self.streamers` which
        will get added to the active set.

        Implementation Required in any child class.

        Parameters
        ----------
        idx : int or None
            The index is passed along so a child class can use it.
            (The index is not required for a random stream as in PoissonMux,
             but would be required for RoundRobin mux).
        """
        raise NotImplementedError("_new_stream_index() must be implemented in"
                                  " a child class.")

    def _next_sample_index(self):
        """Returns the index in self.streams_ for the streamer from which
        to try to draw the next sample.

        Implementation required in any child class.
        """
        raise NotImplementedError("_next_sample_index() must be implemented in"
                                  " a child class.")

    def _new_stream(self, idx):
        '''Randomly select and create a new stream.

        Parameters
        ----------
        idx : int, [0:n_streams - 1]
            The stream index to replace
        '''
        # Choose the stream index from the candidate pool
        self.stream_idxs_[idx] = self._new_stream_index(idx)

        # Activate the Streamer and get the weights
        self.streams_[idx], self.stream_weights_[idx] = (
            self._activate_stream(self.stream_idxs_[idx]))

        # Reset the sample count to zero
        self.stream_counts_[idx] = 0


class PoissonMux(BaseMux):
    '''Stochastic Mux

    Examples
    --------
    >>> # Create a collection of streamers
    >>> seeds = [pescador.Streamer(my_generator) for i in range(10)]
    >>> # Multiplex them together into a single streamer
    >>> # Use at most 3 streams at once
    >>> mux = pescador.PoissonMux(seeds, k=3)
    >>> for batch in mux():
    ...     MY_FUNCTION(batch)

    PoissonMux([stream, range(8), stream2])
    '''
    def __init__(self, streamers, k,
                 rate=256.0, weights=None,
                 mode="with_replacement",
                 prune_empty_streams=True,
                 random_state=None):
        """Given an array (pool) of streamer types, do the following:

        1. Select ``k`` streams at random to iterate from
        2. Assign each activated stream a sample count ~ Poisson(rate)
        3. Yield samples from the streams by randomly multiplexing
           from the active set.
        4. When a stream is exhausted, select a new one from `streamers`.

        Parameters
        ----------
        streamers : iterable of streamers
            The collection of streamer-type objects

        k : int > 0
            The number of streams to keep active at any time.

        rate : float > 0 or None
            Rate parameter for the Poisson distribution governing sample counts
            for individual streams.
            If ``None``, sample infinitely from each stream.

        weights : np.ndarray or None
            Optional weighting for ``streamers``.
            If ``None``, then weights are assumed to be uniform.
            Otherwise, ``weights[i]`` defines the sampling proportion
            of ``streamers[i]``.

            Must have the same length as ``streamers``.

        mode : ["with_replacement", "single_active", "exhaustive"]
            with_replacement
                Sample streamers with replacement.  This allows a single
                stream to be used multiple times (even simultaneously).
                Streams are sampled independently and indefinitely.

            single_active
                This configuration allows a stream to be active at most once
                at any time.

            exhaustive
                Each streamer is consumed at most once and never revisited.
                Run every selected stream once to exhaustion.

        prune_empty_streams : bool
            Disable streamers that produce no data. See `BaseMux`

        random_state : None, int, or np.random.RandomState
            See `BaseMux`
        """
        self.mode = mode
        self.rate = rate

        if self.mode not in [
                "with_replacement", "single_active", "exhaustive"]:
            raise PescadorError("{} is not a valid mode for PoissonMux".format(
                self.mode))

        super(PoissonMux, self).__init__(
            streamers, k, weights=weights,
            random_state=random_state, prune_empty_streams=prune_empty_streams)

    def _new_stream_index(self, idx=None):
        """Returns a random streamer index from `self.streamers`,
        given the current distribution.
        """
        return self.rng.choice(
            self.n_streams, p=self.distribution_)

    def _next_sample_index(self):
        """PoissonMux chooses it's next sample stream randomly"""
        return self.rng.choice(self.k, p=(self.stream_weights_ /
                                          self.weight_norm_))

    def _on_stream_exhausted(self, idx):
        # This is the same as
        #  if self.revive and not self.with_replacement in the original Mux
        if self.mode == "single_active":
            # If we need to revive a seed, give it the max
            # current probability
            if self.distribution_.any():
                self.distribution_[self.stream_idxs_[idx]] = (
                    np.max(self.distribution_))
            else:
                self.distribution_[self.stream_idxs_[idx]] = 1.0

    def _n_samples_to_stream(self):
        "Returns rate or none."
        if self.rate is not None:
            return 1 + self.rng.poisson(lam=self.rate)
        else:
            return None

    def _activate_stream(self, idx):
        '''Randomly select and create a stream.

        Parameters
        ----------
        idx : int, [0:n_streams - 1]
            The stream index to replace
        '''
        streamer, weight = super(PoissonMux, self)._activate_stream(idx)

        # If we're sampling without replacement, zero this one out
        # This effectively disables this stream as soon as it is chosen,
        # preventing it from being chosen again (unless it is revived)
        # if not self.with_replacement:
        if self.mode != "with_replacement":
            self.distribution_[idx] = 0.0

            # Correct the distribution
            if (self.distribution_ > 0).any():
                self.distribution_[:] /= np.sum(self.distribution_)

        return streamer, weight


class ShuffledMux(PoissonMux):
    """A variation on a mux, which takes N streamers, and samples
    from them equally, guaranteeing all N streamers to be "active",
    unlike the base Mux, which randomly chooses streams when activating.

    TODO Does this need to implement things directly, or is subclassing
    PoissonMux okay?
    """
    def __init__(self, streamers, rate=None, weights=None,
                 random_state=None, prune_empty_streams=True):
        super(ShuffledMux, self).__init__(
            streamers, k=len(streamers), rate=rate,
            weights=weights,
            mode="single_active",
            random_state=random_state,
            prune_empty_streams=prune_empty_streams)


class RoundRobinMux(BaseMux):
    """A Mux which iterates over all streamers in strict order.

    TODO: (maybe) handle stream exhaustion?

    Examples
    --------
    >>> a = pescador.Streamer("a")
    >>> b = pescador.Streamer("b")
    >>> c = pescador.Streamer("c")
    >>> mux = pescador.RoundRobinMux([a, b, c])
    >>> print("".join(mux.iterate(9)))
    "abcabcabc"
    """
    def __init__(self, streamers, random_state=None,
                 prune_empty_streams=True):
        super(RoundRobinMux, self).__init__(
            streamers, k=len(streamers),
            random_state=random_state,
            prune_empty_streams=prune_empty_streams)

    def activate(self):
        super(RoundRobinMux, self).activate()
        self.active_index_ = 0

    def deactivate(self):
        super(RoundRobinMux, self).deactivate()
        self.active_index_ = None

    def _new_stream_index(self, idx=None):
        """For RoundRobinMux, we simply wish to have the index of the same
        streamer in `self.streamers`, thereore just return idx.
        """
        return idx

    def _next_sample_index(self):
        """Rotates through each active sampler by incrementing the index"""
        idx = self.active_index_
        self.active_index_ += 1
        if self.active_index_ >= len(self.streamers):
            self.active_index_ = 0

        return idx


class ChainMux(BaseMux):
    """As in itertools.chain(). Runs the first streamer to exhaustion,
    then the second, then the third, etc. k=1.

    Examples
    --------
    >>> a = pescador.Streamer("abc")
    >>> b = pescador.Streamer("def")
    >>> mux = pescador.mux.ChainMux([a, b], mode="exhaustive")
    >>> "".join(list(mux.iterate()))
    "abcdef"

    >>> a = pescador.Streamer("abc")
    >>> b = pescador.Streamer("def")
    >>> mux = pescador.mux.ChainMux([a, b], mode="with_replacement")
    >>> "".join(list(mux.iterate(max_iter=12)))
    "abcdefabcdef"
    """
    def __init__(self, streamers, mode="exhaustive",
                 random_state=None,
                 prune_empty_streams=True):
        """
        Parameters
        ----------
        streamers :

        mode : ["exhaustive", "with_replacement"]
            `exhaustive`
                `ChainMux will exit after each stream has been exhausted.

            `with_replacement`
                `ChainMux will restart from the beginning after each
                streamer has been run to exhaustion.
        """
        super(ChainMux, self).__init__(
            streamers, k=1, random_state=random_state,
            prune_empty_streams=prune_empty_streams)

        self.mode = mode

    def activate(self):
        # This streamer sets it to None so the first streamer knows
        #  it hasn't been used yet.
        self.active_index_ = None

        super(ChainMux, self).activate()

    def deactivate(self):
        super(ChainMux, self).deactivate()
        self.active_index_ = None

    def _new_stream_index(self, idx=None):
        """Just increment the active stream every time one is requested."""
        # Streamer is starting
        if self.active_index_ is None:
            self.active_index_ = 0

        else:
            self.active_index_ += 1

        # Move to the next streamer
        if self.active_index_ >= len(self.streamers):
            self.active_index_ = 0

        return self.active_index_

    def _next_sample_index(self):
        """k==1, this is always 0."""
        return 0

    def _activate_stream(self, idx):
        '''Activate the next stream.

        Parameters
        ----------
        idx : int, [0:n_streams - 1]
            The stream index to replace
        '''
        streamer, weight = super(ChainMux, self)._activate_stream(idx)

        # If we're sampling without replacement, zero this one out
        # This effectively disables this stream as soon as it is chosen,
        # preventing it from being chosen again (unless it is revived)
        # if not self.with_replacement:
        if self.mode != "with_replacement":
            self.distribution_[idx] = 0.0

            # Correct the distribution
            if (self.distribution_ > 0).any():
                self.distribution_[:] /= np.sum(self.distribution_)

        return streamer, weight
