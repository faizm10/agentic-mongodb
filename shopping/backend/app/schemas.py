"""Request models for the public API."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class CartItemIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=80)
    # Range is enforced by the pricing domain so the error is a precise INVALID_QUANTITY.
    quantity: int


class QuoteRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    items: list[CartItemIn] = Field(default_factory=list, max_length=50)
    promo_code: str | None = Field(default=None, max_length=32)
    shipping_method: Literal["standard", "express"] = "standard"


class CustomerIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    full_name: str = Field(min_length=2, max_length=120)
    email: str = Field(max_length=254)
    phone: str | None = Field(default=None, max_length=40)

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        if not _EMAIL_RE.match(value):
            raise ValueError("must be a valid email address")
        return value.lower()


class ShippingAddressIn(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    line1: str = Field(min_length=3, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=2, max_length=100)
    state: str = Field(min_length=2, max_length=100)
    postal_code: str = Field(min_length=3, max_length=20)
    country: str = Field(default="US", min_length=2, max_length=56)


class CheckoutRequest(QuoteRequest):
    customer: CustomerIn
    shipping_address: ShippingAddressIn
