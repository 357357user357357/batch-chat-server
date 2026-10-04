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
