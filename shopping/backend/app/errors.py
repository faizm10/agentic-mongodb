"""Structured, machine-readable errors for the NovaMart API.

Every domain failure carries a stable ``code``, the ``component`` that raised it,
a human ``message``, structured ``details`` and an optional ``hint``. The API
returns them verbatim so that humans and OpsRelay's AI investigator can diagnose
a failure without having to read stack traces.
"""

from __future__ import annotations

from typing import Any


class NovaMartError(Exception):
    status_code: int = 400
    code: str = "NOVAMART_ERROR"
    component: str = "checkout"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.hint = hint

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "component": self.component,
            "message": self.message,
            "details": self.details,
        }
        if self.hint:
            payload["hint"] = self.hint
        return payload


class EmptyCartError(NovaMartError):
    status_code = 400
    code = "EMPTY_CART"
    component = "checkout"


class InvalidQuantityError(NovaMartError):
    status_code = 422
    code = "INVALID_QUANTITY"
    component = "pricing"


class InvalidPromoCodeError(NovaMartError):
    status_code = 400
    code = "INVALID_PROMO_CODE"
    component = "pricing"


class InvalidShippingMethodError(NovaMartError):
    status_code = 422
    code = "INVALID_SHIPPING_METHOD"
    component = "pricing"


class ProductNotFoundError(NovaMartError):
    status_code = 404
    code = "PRODUCT_NOT_FOUND"
    component = "catalog"


class InsufficientStockError(NovaMartError):
    status_code = 409
    code = "INSUFFICIENT_STOCK"
    component = "inventory"


class OrderNotFoundError(NovaMartError):
    status_code = 404
    code = "ORDER_NOT_FOUND"
    component = "orders"


class OrderStoreError(NovaMartError):
    status_code = 503
    code = "ORDER_STORE_UNAVAILABLE"
    component = "orders"


class PaymentUnavailableError(NovaMartError):
    status_code = 503
    code = "PAYMENT_UNAVAILABLE"
    component = "checkout"


class CheckoutConflictError(NovaMartError):
    status_code = 409
    code = "CHECKOUT_ID_CONFLICT"
    component = "checkout"


class ConfirmationUnavailableError(NovaMartError):
    status_code = 504
    code = "CONFIRMATION_UNAVAILABLE"
    component = "confirmations"


class CheckoutNotFoundError(NovaMartError):
    status_code = 404
    code = "CHECKOUT_NOT_FOUND"
    component = "checkout"
