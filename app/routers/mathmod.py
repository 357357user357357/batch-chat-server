"""Exact modular-form math over HTTP: Hecke operators as a service.

Backed by app/services/math_modular.py (stdlib-only, MIT) — "Hecke operator
capable" math without Sage's GPL. Endpoints are auth-gated like every other
user-facing route. precision/n are capped so a curious payload cannot hog
the server: the math is exact integer work, the caps keep it bounded.
"""

from __future__ import annotations

from fractions import Fraction

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.security import get_account_id
from app.services import math_modular as mm

router = APIRouter(prefix="/api/math", tags=["math"])

_MAX_PRECISION = 600
_MAX_N = 10**6
_MIN_WEIGHT = 4
_MAX_WEIGHT = 24


class HeckeRequest(BaseModel):
    form: str = Field("delta", description='"delta" or "eisenstein"')
    weight: int | None = Field(None, description="even k in [4, 24] for eisenstein")
    precision: int = Field(30, ge=2, le=_MAX_PRECISION)
    n: int = Field(1, ge=1, le=_MAX_N)


@router.get("/forms")
def forms() -> dict:
    return {
        "forms": [
            {
                "id": "delta",
                "weight": 12,
                "level": 1,
                "description": (
                    "Modular discriminant (E4^3 - E6^2)/1728. Coefficients are "
                    "the Ramanujan tau numbers; Hecke eigenform, so "
                    "T_n Delta = tau(n) Delta exactly."
                ),
            },
            {
                "id": "eisenstein",
                "weight": None,
                "level": 1,
                "description": (
                    "E_k for even k in [4, 24]. Hecke eigenform with "
                    "eigenvalue sigma_{k-1}(n)."
                ),
            },
        ],
        "limits": {"precision": _MAX_PRECISION, "n": _MAX_N},
        "notation": (
            "coefficients a(0..N-1) of f(q) = sum a(i) q^i; T_n f is returned "
            "only up to the precision the input actually determines"
        ),
    }


@router.post("/hecke")
def hecke_endpoint(
    payload: HeckeRequest, account_id: str = Depends(get_account_id)
) -> dict:
    if payload.form == "delta":
        f = mm.delta(payload.precision)
        weight = 12
    elif payload.form == "eisenstein":
        if (
            payload.weight is None
            or payload.weight % 2
            or not (_MIN_WEIGHT <= payload.weight <= _MAX_WEIGHT)
        ):
            raise HTTPException(
                status_code=422,
                detail=f"eisenstein needs an even weight in [{_MIN_WEIGHT}, {_MAX_WEIGHT}]",
            )
        f = mm.eisenstein(payload.weight, payload.precision)
        weight = payload.weight
    else:
        raise HTTPException(status_code=422, detail='form must be "delta" or "eisenstein"')

    try:
        result = mm.hecke(f, payload.n)
        coeffs = result.as_ints()
    except ValueError as exc:  # defensive: non-integral would mean a bug
        raise HTTPException(status_code=500, detail=f"non-integral result: {exc}")

    eigenvalue = None
    if result.precision() > 1 and f[1] != 0:
        lam = result[1] / f[1]
        if lam.denominator == 1:
            eigenvalue = int(lam)

    expected = mm.sigma(payload.n, weight - 1) if payload.form == "eisenstein" else None
    is_eigen = None
    if payload.form == "eisenstein" and eigenvalue is not None:
        is_eigen = eigenvalue == expected

    return {
        "form": payload.form,
        "weight": weight,
        "n": payload.n,
        "precision": result.precision(),
        "input_precision": f.precision(),
        "coefficients": coeffs,
        "eigenvalue": eigenvalue,
        "expected_eigenvalue": expected,
        "is_eigen": is_eigen,
    }
