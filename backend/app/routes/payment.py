import asyncio

import stripe
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.dependencies import require_verified_email
from app.database import get_db
from app.models.payment import Payment
from app.models.user import User
from app.services import everyorg, pledge

stripe.api_key = settings.stripe_secret_key

router = APIRouter(tags=["payment"])


class ClientSecretResponse(BaseModel):
    client_secret: str


class PaymentConfigResponse(BaseModel):
    publishable_key: str


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
    description: str | None = None
    location: str | None = None
    # "stripe" = Connect account created on this platform (transfers run
    # automatically once onboarded); "everyorg" = public nonprofit (donation
    # completed via a prefilled Every.org link after the charge).
    source: str = "stripe"


class CharityCreateRequest(BaseModel):
    name: str
    email: str


class CharityCreateResponse(BaseModel):
    id: str
    name: str
    onboarding_url: str


class PaymentHistoryItem(BaseModel):
    id: str
    goal_id: str
    amount: int
    currency: str
    status: str
    stripe_payment_intent_id: str | None = None
    stripe_transfer_id: str | None = None
    created_at: str


async def _get_or_create_stripe_customer(user: User, db: AsyncSession) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(
        email=user.email,
        name=user.display_name,
        metadata={"user_id": str(user.id)},
    )
    user.stripe_customer_id = customer.id
    await db.commit()
    return customer.id


@router.get("/api/payment/config", response_model=PaymentConfigResponse)
async def payment_config(current_user: User = Depends(require_verified_email)):
    """Publishable key for the frontend's Stripe.js card-entry flow."""
    if not settings.stripe_publishable_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    return PaymentConfigResponse(publishable_key=settings.stripe_publishable_key)


@router.post("/api/payment/setup-intent", response_model=ClientSecretResponse)
async def create_setup_intent(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    customer_id = await _get_or_create_stripe_customer(current_user, db)

    setup_intent = stripe.SetupIntent.create(customer=customer_id)
    return ClientSecretResponse(client_secret=setup_intent.client_secret)


@router.get("/api/payment/methods", response_model=list[PaymentMethodResponse])
async def list_payment_methods(
    current_user: User = Depends(require_verified_email),
    db: AsyncSession = Depends(get_db),
):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    customer_id = await _get_or_create_stripe_customer(current_user, db)

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


@router.delete(
    "/api/payment/methods/{method_id}", response_model=DeletePaymentMethodResponse
)
async def delete_payment_method(
    method_id: str,
    current_user: User = Depends(require_verified_email),
):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    if not current_user.stripe_customer_id:
        raise HTTPException(status_code=404, detail="Payment method not found")

    try:
        pm = stripe.PaymentMethod.retrieve(method_id)
    except stripe.error.StripeError:
        raise HTTPException(status_code=404, detail="Payment method not found")

    if pm.customer != current_user.stripe_customer_id:
        raise HTTPException(status_code=404, detail="Payment method not found")

    try:
        detached = stripe.PaymentMethod.detach(method_id)
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return DeletePaymentMethodResponse(id=detached.id, detached=True)


@router.get("/api/payments", response_model=list[PaymentHistoryItem])
async def list_payments(
    current_user: User = Depends(require_verified_email),
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
    current_user: User = Depends(require_verified_email),
):
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    # NB: Account.list takes no `type` filter — passing one is an
    # InvalidRequestError ("Received unknown parameter: type"), which used to
    # 500 this endpoint unconditionally. List and filter in code instead.
    #
    # The Stripe SDK call is synchronous — run it in a thread and IN PARALLEL
    # with the public-charity search; serial round-trips made every keystroke
    # of the picker feel like multiple seconds.
    async def _stripe_accounts():
        try:
            return await asyncio.to_thread(stripe.Account.list, limit=10)
        except stripe.error.StripeError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Stripe account listing failed: {e.user_message or e}",
            )

    # Pledge.to is preferred when configured — its donations disburse
    # automatically — falling back to Every.org (donate-link flow).
    async def _public_charities():
        if not q:
            return []
        public = await pledge.search_organizations(q)
        if not public:
            public = await everyorg.search_nonprofits(q)
        return public

    accounts, public = await asyncio.gather(_stripe_accounts(), _public_charities())

    results = []
    for account in accounts.data:
        name = ""
        if account.business_profile and account.business_profile.name:
            name = account.business_profile.name
        if not q or q.lower() in name.lower():
            results.append(CharityItem(id=account.id, name=name, source="stripe"))
    for np in public:
        results.append(CharityItem(**np))
    return results


@router.get("/api/charities/lookup", response_model=CharityItem)
async def lookup_charity(
    id: str = Query(...),
    current_user: User = Depends(require_verified_email),
):
    """Resolve a stored charity_id (acct_…, everyorg:… or pledge:…) to a name."""
    if everyorg.is_everyorg_id(id):
        name = await everyorg.get_nonprofit_name(id)
        if not name:
            raise HTTPException(status_code=404, detail="Charity not found")
        return CharityItem(id=id, name=name, source="everyorg")

    if pledge.is_pledge_id(id):
        name = await pledge.get_organization_name(id)
        if not name:
            raise HTTPException(status_code=404, detail="Charity not found")
        return CharityItem(id=id, name=name, source="pledge")

    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    try:
        account = stripe.Account.retrieve(id)
    except stripe.error.StripeError:
        raise HTTPException(status_code=404, detail="Charity not found")
    name = ""
    if account.business_profile and account.business_profile.name:
        name = account.business_profile.name
    return CharityItem(id=id, name=name or id, source="stripe")


@router.post("/api/charities", response_model=CharityCreateResponse, status_code=201)
async def create_charity(
    body: CharityCreateRequest,
    current_user: User = Depends(require_verified_email),
):
    """Create a Stripe Connect (Express) account to receive pledges.

    Returns an onboarding link the recipient must complete before transfers
    can reach them. Requires Connect to be enabled on the platform account —
    if it isn't, Stripe's error is surfaced as a 502 with its message.
    """
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    try:
        account = stripe.Account.create(
            type="express",
            email=body.email,
            business_profile={"name": body.name},
            capabilities={"transfers": {"requested": True}},
            metadata={"created_by_user_id": str(current_user.id)},
        )
        link = stripe.AccountLink.create(
            account=account.id,
            refresh_url=settings.frontend_url,
            return_url=settings.frontend_url,
            type="account_onboarding",
        )
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=502, detail=f"Stripe Connect error: {e.user_message or e}"
        )

    return CharityCreateResponse(id=account.id, name=body.name, onboarding_url=link.url)
