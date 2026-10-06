"""Level-1 modular forms as q-expansions, with Hecke operators.

Pure-stdlib replacement for "just use Sage" in the narrow, common case:
exact rational q-expansion arithmetic, Eisenstein series, the modular
discriminant, and the Hecke/U_p/V_p operators. Everything is exact
(``fractions.Fraction``), so eigenvalues come out as exact integers and the
test suite pins them against classical values (E8 = E4^2, the Ramanujan tau
numbers).

Design notes:
- A QExpansion is a finite coefficient list; every operation truncates to the
  shorter precision, so operator compositions keep the input precision N.
- General T_n is built from prime powers: T_m T_n = T_mn for gcd(m,n)=1 and
  T_{p^e} = T_p T_{p^{e-1}} - p^{k-1} T_{p^{e-2}} (trivial character).
- T_n is level 1 only; U_p / V_p act on any level. Anything beyond raises,
  honestly.

A python-flint backend (Apache/BSD wrappers over LGPL FLINT) can later
accelerate the coefficient arithmetic behind this same API.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

from fractions import Fraction
from functools import lru_cache
from math import comb

__all__ = [
    "QExpansion",
    "bernoulli",
    "eisenstein",
    "delta",
    "hecke",
    "u_p",
    "v_p",
    "sigma",
    "prime_factorization",
]


# ------------------------------------------------------------------ integers


@lru_cache(maxsize=64)
def bernoulli(m: int) -> Fraction:
    """Bernoulli number B_m (even convention: B_1 = -1/2)."""
    if m < 0:
        raise ValueError("m must be >= 0")
    if m == 0:
        return Fraction(1)
    if m == 1:
        return Fraction(-1, 2)
    if m % 2 == 1:
        return Fraction(0)
    total = Fraction(0)
    for j in range(m):
        total += comb(m + 1, j) * bernoulli(j)
    return -total / (m + 1)


def prime_factorization(n: int) -> dict[int, int]:
    """n as {prime: exponent} by trial division (fine for n up to ~1e12)."""
    if n < 1:
        raise ValueError("n must be >= 1")
    factors: dict[int, int] = {}
    d = 2
    while d * d <= n:
        while n % d == 0:
            factors[d] = factors.get(d, 0) + 1
            n //= d
        d += 1 if d == 2 else 2
    if n > 1:
        factors[n] = factors.get(n, 0) + 1
    return factors


def sigma(n: int, s: int) -> int:
    """Divisor sum sigma_s(n) = sum of d^s over positive divisors d of n."""
    if n < 1:
        raise ValueError("n must be >= 1")
    total = 0
    d = 1
    while d * d <= n:
        if n % d == 0:
            total += d**s
            other = n // d
            if other != d:
                total += other**s
        d += 1
    return total


# --------------------------------------------------------------- q-expansions


class QExpansion:
    """f(q) = sum_{n=0..N-1} a(n) q^n of weight `weight` on Gamma0(level).

    Coefficients are exact Fractions; q-integral forms can be read back with
    as_ints(). All arithmetic truncates to the shorter precision.
    """

    __slots__ = ("weight", "level", "coeffs")

    def __init__(self, weight: int, coeffs, level: int = 1):
        if weight < 0:
            raise ValueError("weight must be >= 0")
        self.weight = weight
        self.level = level
        self.coeffs = [Fraction(c) for c in coeffs]

    def precision(self) -> int:
        return len(self.coeffs)

    def __getitem__(self, n: int) -> Fraction:
        return self.coeffs[n] if 0 <= n < len(self.coeffs) else Fraction(0)

    def as_ints(self) -> list[int]:
        out = []
        for c in self.coeffs:
            if c.denominator != 1:
                raise ValueError(f"non-integral coefficient {c}")
            out.append(int(c))
        return out

    def _check(self, other: "QExpansion") -> None:
        if not isinstance(other, QExpansion):
            raise TypeError("expected a QExpansion")
        if self.weight != other.weight:
            raise ValueError("weight mismatch")
        if self.level != other.level:
            raise ValueError("level mismatch")

    def __add__(self, other: "QExpansion") -> "QExpansion":
        self._check(other)
        n = min(self.precision(), other.precision())
        return QExpansion(
            self.weight, [self[i] + other[i] for i in range(n)], self.level
        )

    def __sub__(self, other: "QExpansion") -> "QExpansion":
        self._check(other)
        n = min(self.precision(), other.precision())
        return QExpansion(
            self.weight, [self[i] - other[i] for i in range(n)], self.level
        )

    def __mul__(self, other: "QExpansion") -> "QExpansion":
        if not isinstance(other, QExpansion):
            raise TypeError("expected a QExpansion")
        if self.level != other.level:
            raise ValueError("level mismatch")  # weights ADD in the product
        n = min(self.precision(), other.precision())
        out = [Fraction(0)] * n
        for i in range(n):
            a = self.coeffs[i]
            if a == 0:
                continue
            for j in range(n - i):
                b = other.coeffs[j]
                if b != 0:
                    out[i + j] += a * b
        return QExpansion(self.weight + other.weight, out, self.level)

    def __pow__(self, exponent: int) -> "QExpansion":
        if exponent < 1:
            raise ValueError("exponent must be >= 1")
        result = self
        for _ in range(exponent - 1):
            result = result * self
        return result

    def scale(self, c) -> "QExpansion":
        c = Fraction(c)
        return QExpansion(self.weight, [c * a for a in self.coeffs], self.level)

    def __repr__(self) -> str:
        return (
            f"QExpansion(weight={self.weight}, level={self.level}, "
            f"N={self.precision()})"
        )


# ------------------------------------------------------------- standard forms


def eisenstein(k: int, precision: int) -> QExpansion:
    """Level-1 Eisenstein series E_k for even k >= 4:

        E_k = 1 - (2k / B_k) * sum_{n>=1} sigma_{k-1}(n) q^n
    """
    if k < 4 or k % 2:
        raise ValueError("k must be even and >= 4")
    if precision < 1:
        raise ValueError("precision must be >= 1")
    c = Fraction(-2 * k, 1) / bernoulli(k)
    coeffs = [Fraction(1)]
    for n in range(1, precision):
        coeffs.append(c * sigma(n, k - 1))
    return QExpansion(k, coeffs)


def delta(precision: int) -> QExpansion:
    """The modular discriminant, weight 12, level 1:

        Delta = q * prod (1 - q^n)^24 = (E4^3 - E6^2) / 1728

    Its coefficients are the Ramanujan tau numbers (a(0) = 0).
    """
    e4 = eisenstein(4, precision)
    e6 = eisenstein(6, precision)
    return (e4**3 - e6**2).scale(Fraction(1, 1728))


# ------------------------------------------------------------ Hecke operators


def v_p(f: QExpansion, p: int) -> QExpansion:
    """V_p: f(q) -> f(q^p) — the a(n) coefficient lands on q^{pn}."""
    if p < 1:
        raise ValueError("p must be >= 1")
    n = f.precision()
    out = [Fraction(0)] * n
    for i, a in enumerate(f.coeffs):
        if p * i < n:
            out[p * i] = a
    return QExpansion(f.weight, out, f.level)


def u_p(f: QExpansion, p: int) -> QExpansion:
    """U_p: a(n) -> a(p n), constant term preserved (any level, any prime).

    Coefficient i needs a(p*i), so the result carries precision
    (N-1)//p + 1 — the part the input expansion actually determines.
    """
    if p < 1:
        raise ValueError("p must be >= 1")
    n = f.precision()
    c = f.coeffs
    out = [c[p * i] for i in range((n - 1) // p + 1)]
    return QExpansion(f.weight, out, f.level)


def _hecke_prime(f: QExpansion, p: int) -> QExpansion:
    """T_p for p not dividing the level: a(n) -> a(pn) + p^{k-1} a(n/p).

    Coefficient i needs a(p*i), so the result carries precision
    (N-1)//p + 1 — the part the input expansion actually determines.
    """
    k = f.weight
    n = f.precision()
    c = f.coeffs
    factor = Fraction(p) ** (k - 1)
    out = []
    for i in range((n - 1) // p + 1):
        val = c[p * i]
        if i % p == 0:
            val += factor * c[i // p]
        out.append(val)
    return QExpansion(f.weight, out, f.level)


def _hecke_prime_power(f: QExpansion, p: int, e: int) -> QExpansion:
    """T_{p^e} via T_{p^e} = T_p T_{p^{e-1}} - p^{k-1} T_{p^{e-2}}."""
    if e == 0:
        return f
    t_prev = f
    t_cur = _hecke_prime(f, p)
    for _ in range(2, e + 1):
        t_prev, t_cur = t_cur, _hecke_prime(t_cur, p) - t_prev.scale(
            Fraction(p) ** (f.weight - 1)
        )
    return t_cur


def hecke(f: QExpansion, n: int) -> QExpansion:
    """Hecke operator T_n on a level-1 q-expansion (exact).

    Composed from prime powers using T_m T_n = T_mn for gcd(m, n) = 1 and
    T_{p^e} = T_p T_{p^{e-1}} - p^{k-1} T_{p^{e-2}} (trivial character).

    Precision shrinks honestly: T_p needs a(p*i) for output coefficient i,
    so each application returns only the part the input determines (ask for
    a wider `precision` up front if you need a wide trusted window).
    """
    if f.level != 1:
        raise NotImplementedError(
            "T_n is implemented for level 1 only; use u_p/v_p otherwise"
        )
    if n < 1:
        raise ValueError("n must be >= 1")
    result = f
    for p, e in sorted(prime_factorization(n).items()):
        result = _hecke_prime_power(result, p, e)
    return result


# ------------------------------------------------------------- L-functions
#
# Honest floating-point L-values for level-1 forms, two independent routes:
#
#   l_value(f, s, terms)      partial Dirichlet series  sum a(n) n^-s
#                             — only meaningful where it converges
#                             (s > (weight+1)/2 for normalized cusp forms);
#                             it is a truncation, and we say so.
#
#   completed_l(f, s, steps)  the completed Mellin integral
#                             Lambda(f,s) = int_0^inf f(iy) y^(s-1) dy,
#                             folded onto [1, inf) via modularity
#                             f(i/y) = i^k y^k f(iy):
#                               Lambda(f,s) = int_1^inf f(iy)(y^(s-1)
#                                                           + i^k y^(k-s-1)) dy
#                             — convergent for EVERY s, including the
#                             critical strip. Cusp forms only (a_0 = 0),
#                             level 1 only (the split uses full SL2Z
#                             modularity).
#
# The two routes agree where both apply; and Lambda(f, s) = i^k Lambda(f, k-s)
# gives the functional equation, so l_any reaches the critical strip too.

import math as _math


def l_value(f: "QExpansion", s: float, terms: int | None = None) -> float:
    """Truncated Dirichlet series sum_{n=1..terms} a(n) / n^s as float.

    A truncation, not an analytic continuation: meaningful where the series
    converges (s > (weight+1)/2 for cusp eigenforms like Delta; s > weight
    for Eisenstein coefficients sigma_{k-1}). `terms` defaults to the
    expansion precision.
    """
    if s <= 0:
        raise ValueError("s must be > 0")
    if f[0] != 0:
        raise ValueError("Dirichlet series of a form with a nonzero constant term diverges")
    n_terms = f.precision() - 1 if terms is None else min(terms, f.precision() - 1)
    total = 0.0
    for n in range(1, n_terms + 1):
        a = f[n]
        if a:
            total += float(a) / n**s
    return total


def _f_at_iy(f: "QExpansion", y: float) -> float:
    """sum_{n>=1} a(n) e^{-2 pi n y} — the cusp-form value on the imaginary
    axis, from the stored coefficients (geometrically decaying in y >= 1)."""
    total = 0.0
    for n in range(1, f.precision()):
        a = f[n]
        if a:
            total += float(a) * _math.exp(-2.0 * _math.pi * n * y)
    return total


def completed_l(f: "QExpansion", s: float, steps: int = 2048) -> complex:
    """Lambda(f, s) = int_0^inf f(iy) y^{s-1} dy for a level-1 cusp form.

    Computed on [1, inf) via modularity (see module notes), so it works at
    every s — inside the critical strip included. Composite Simpson with
    `steps` even subintervals on [1, 25]; the exp(-2 pi y) decay makes the
    tail beyond 25 far below double precision.
    """
    if f.level != 1:
        raise NotImplementedError("completed_l is implemented for level 1 only")
    if f[0] != 0:
        raise ValueError("completed_l requires a cusp form (constant term 0)")
    k = f.weight
    if steps % 2:
        steps += 1
    ik = complex(0, 1) ** k
    y_max = 25.0
    h = (y_max - 1.0) / steps

    def integrand(y: float) -> complex:
        return _f_at_iy(f, y) * (y ** (s - 1.0) + ik * y ** (k - s - 1.0))

    total = integrand(1.0) + integrand(y_max)
    for i in range(1, steps):
        total += integrand(1.0 + i * h) * (4 if i % 2 else 2)
    return total * h / 3.0


def l_any(f: "QExpansion", s: float, terms: int | None = None, steps: int = 2048) -> float:
    """L(f, s) at ANY positive s, level-1 cusp forms.

    Right of the convergence wall (s > (weight+1)/2) this is the honest
    partial Dirichlet sum; at or left of it, the value is the analytic
    continuation read off the convergent Mellin integral (completed_l).
    Cross-check: the functional equation Lambda(f, s) = i^k Lambda(f, k-s)
    holds between two completed_l evaluations with different integrands.
    """
    if f.level != 1 or f[0] != 0:
        raise ValueError("l_any requires a level-1 cusp form")
    wall = (f.weight + 1) / 2.0
    if s > wall:
        return l_value(f, s, terms)
    lam = completed_l(f, s, steps)
    return lam.real * (2.0 * _math.pi) ** s / _math.gamma(s)
