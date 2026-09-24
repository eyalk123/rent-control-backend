"""The billing catalog: which store product is which plan.

**This is the one place a product identifier is mapped to a plan.** Store product
identifiers are created by hand in Paddle, App Store Connect and Play Console, so this
table is the seam where three consoles meet one codebase. Adding a product in any console
means adding a line here, and nowhere else.

It is an explicit table rather than something parsed out of the identifier string: a
naming convention silently broken in a console would otherwise resolve to a
plausible-looking wrong plan, where a missing entry fails loudly as `unmapped` and changes
nothing (see `revenuecat_service`).

The RevenueCat **entitlement** identifiers are the plan names themselves (`tier_3_8`,
`tier_9_15`, `tier_16_plus`), so the RevenueCat dashboard and the `subscriptions` table
talk about the same things. The webhook still decides by product, not by entitlement: a
product carries the billing period as well as the band, and an entitlement does not.

Identifier formats per store, for when the mobile products exist:
- Paddle: the price id, `pri_…` (a Paddle *price* is a RevenueCat *product*).
- App Store: the product id as typed in App Store Connect.
- Play: `<subscriptionId>:<basePlanId>` — RevenueCat reports both halves.
"""
from app.services import entitlement_service as ent

PRODUCT_PLANS: dict[str, tuple[str, str]] = {
    # Paddle (live). Prices: $15/$150, $20/$200, $25/$250.
    "pri_01m39bzfjqd489x258g3srtg9v": (ent.PLAN_TIER_3_8, "monthly"),
    "pri_01m39c0e8mx96spd6sagf972ny": (ent.PLAN_TIER_3_8, "yearly"),
    "pri_01m39c1sm362n1j2jvd7jhxs32": (ent.PLAN_TIER_9_15, "monthly"),
    "pri_01m39c2j8af8j4vd49n2t5hsm6": (ent.PLAN_TIER_9_15, "yearly"),
    "pri_01m39c3qa4597sp255tbbhes17": (ent.PLAN_TIER_16_PLUS, "monthly"),
    "pri_01m39c45cxf0jwj12gb7aq6b7h": (ent.PLAN_TIER_16_PLUS, "yearly"),
}
