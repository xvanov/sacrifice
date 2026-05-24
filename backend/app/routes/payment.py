import stripe
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import get_current_user
from app.database import get_db
from app.models.payment import Payment
from app.models.user import User

stripe.api_key = settings.stripe_secret_key

router = APIRouter(tags=["payment"])


class ClientSecretResponse(BaseModel):
    client_secret: str


class PaymentMethodCard(BaseModel):
    last4: str
    brand: str
    exp_month: int
    exp_year: int


class PaymentMethodResponse(BaseModel):
    id: str
    card: PaymentMethodCard
    billing_name: str | None = None


class DeletePaymentMethodResponse(BaseModel):
    id: str
    detached: bool


class CharityItem(BaseModel):
    id: str
    name: str


class PaymentHistoryItem(BaseModel):
    id: str
    goal_id: str
    amount: int
    currency: str
    status: str
    stripe_payment_intent_id: str | None = None
    stripe_transfer_id: str | None = None
    created_at: str


@router.post("/api/payment/setup-intent", response_model=ClientSecretResponse)
async def create_setup_intent(
    current_user: User = Depends(get_current_user),
):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    if current_user.stripe_customer_id:
        customer_id = current_user.stripe_customer_id
    else:
        customer = stripe.Customer.create(
            email=current_user.email,
            name=current_user.display_name,
            metadata={"user_id": str(current_user.id)},
        )
        customer_id = customer.id

    setup_intent = stripe.SetupIntent.create(customer=customer_id)
    return ClientSecretResponse(client_secret=setup_intent.client_secret)


@router.get("/api/payment/methods", response_model=list[PaymentMethodResponse])
async def list_payment_methods(
    current_user: User = Depends(get_current_user),
):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    customer_id = current_user.stripe_customer_id
    if not customer_id:
        customer = stripe.Customer.create(
            email=current_user.email,
            name=current_user.display_name,
            metadata={"user_id": str(current_user.id)},
        )
        customer_id = customer.id

    methods = stripe.PaymentMethod.list(
        customer=customer_id,
        type="card",
    )
    result = []
    for pm in methods.data:
        result.append(
            PaymentMethodResponse(
                id=pm.id,
                card=PaymentMethodCard(
                    last4=pm.card.last4,
                    brand=pm.card.brand,
                    exp_month=pm.card.exp_month,
                    exp_year=pm.card.exp_year,
                ),
                billing_name=pm.billing_details.name,
            )
        )
    return result


@router.delete("/api/payment/methods/{method_id}", response_model=DeletePaymentMethodResponse)
async def delete_payment_method(
    method_id: str,
    current_user: User = Depends(get_current_user),
):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    try:
        detached = stripe.PaymentMethod.detach(method_id)
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return DeletePaymentMethodResponse(id=detached.id, detached=True)


@router.get("/api/payments", response_model=list[PaymentHistoryItem])
async def list_payments(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Payment)
        .where(Payment.user_id == current_user.id)
        .order_by(Payment.created_at.desc())
    )
    payments = result.scalars().all()
    return [
        PaymentHistoryItem(
            id=str(p.id),
            goal_id=str(p.goal_id),
            amount=p.amount,
            currency=p.currency,
            status=p.status,
            stripe_payment_intent_id=p.stripe_payment_intent_id,
            stripe_transfer_id=p.stripe_transfer_id,
            created_at=p.created_at.isoformat(),
        )
        for p in payments
    ]


@router.get("/api/charities/search", response_model=list[CharityItem])
async def search_charities(
    q: str | None = Query(None),
    current_user: User = Depends(get_current_user),
):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    accounts = stripe.Account.list(
        type="standard",
        limit=10,
    )
    results = []
    for account in accounts.data:
        name = ""
        if account.business_profile and account.business_profile.name:
            name = account.business_profile.name
        if not q or q.lower() in name.lower():
            results.append(CharityItem(id=account.id, name=name))
    return results
