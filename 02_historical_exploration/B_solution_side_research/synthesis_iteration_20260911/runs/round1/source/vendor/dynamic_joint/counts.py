"""Cardinality-coupled channel existence, using public history likelihoods.

For exchangeable channel assignments, P(N|H) is proportional to
prior[N] * elementary_symmetric(L_unknown, N-K) / choose(20,N).
The per-channel likelihood L_f is a working estimate, not a certificate.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class CountPrior:
    probabilities: dict
    directional_fraction: float = .5
    label: str = 'uniform_N_10_to_16_working_prior'

    @classmethod
    def from_config(cls, problem, data=None):
        broad = {n: 1/7 for n in range(10, 17)}
        if not data:
            return cls(broad, 0. if problem == 3 else .5)
        allowed = {'count_probabilities', 'directional_fraction', 'broad_weight', 'label'}
        if set(data)-allowed:
            raise ValueError('unknown prior fields: '+str(sorted(set(data)-allowed)))
        raw = {int(n): float(p) for n, p in data['count_probabilities'].items()}
        if set(raw)-set(range(10, 17)) or any(not math.isfinite(p) or p < 0 for p in raw.values()):
            raise ValueError('prior counts must lie in 10..16 and probabilities be nonnegative')
        if abs(sum(raw.values())-1) > 1e-6:
            raise ValueError('count probabilities must sum to one')
        weight = float(data.get('broad_weight', .35))
        if not math.isfinite(weight) or not 0 < weight <= 1:
            raise ValueError('broad_weight must be in (0,1] to keep legal counts supported')
        direction = 0. if problem == 3 else float(data.get('directional_fraction', .5))
        if problem == 4 and (not math.isfinite(direction) or not 0 < direction < 1):
            raise ValueError('working directional fraction must be strictly between zero and one')
        return cls({n: (1-weight)*raw.get(n, 0)+weight/7 for n in broad}, direction,
                   data.get('label', 'user_supplied_count_prior_with_broad_mixture'))


def coefficients(likelihoods):
    result = [1.] + [0.]*len(likelihoods)
    used = 0
    for value in likelihoods:
        if not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('existence likelihood must lie in [0,1]')
        used += 1
        for j in range(used, 0, -1):
            result[j] += value*result[j-1]
    return result


def posterior(prior, known, likelihoods):
    """Exact cardinality calculation conditional on the supplied working L_f."""
    if not isinstance(known, int) or not 0 <= known <= 16 or known+len(likelihoods) > 20:
        raise ValueError('invalid confirmed/unknown channel counts')
    values = list(likelihoods.values())
    coeff = coefficients(values)
    weights = {}
    for n, probability in prior.probabilities.items():
        missing = n-known
        if 0 <= missing <= len(values):
            weights[n] = probability*coeff[missing]/math.comb(20, n)
    total = math.fsum(weights.values())
    if total <= 0:
        raise ValueError('count constraints have no supported state')
    distribution = {n: w/total for n, w in weights.items()}
    marginals = {}
    for channel, likelihood in likelihoods.items():
        rest = coefficients([v for f, v in likelihoods.items() if f != channel])
        numerator = 0.
        for n, probability in prior.probabilities.items():
            index = n-known-1
            if 0 <= index < len(rest):
                numerator += probability*likelihood*rest[index]/math.comb(20, n)
        marginals[channel] = min(1., max(0., numerator/total))
    return {'known_sources': known, 'unknown_channels': len(values),
            'unseen_min': max(0, 10-known), 'unseen_max': min(16-known, len(values)),
            'expected_unseen': math.fsum(n*p for n, p in distribution.items())-known,
            'count_distribution': distribution, 'channel_presence': marginals,
            'prior_label': prior.label, 'probability_is_certificate': False}
