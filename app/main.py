from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import expense_categories, properties, renters, reports, suppliers, transactions, users
from app.config import settings

app = FastAPI(title="Property Management API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(properties.router, prefix="/properties", tags=["properties"])
app.include_router(renters.router, prefix="/renters", tags=["renters"])
app.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
app.include_router(expense_categories.router, prefix="/expense-categories", tags=["expense-categories"])
app.include_router(suppliers.router, prefix="/suppliers", tags=["suppliers"])
app.include_router(users.router, prefix="/users", tags=["users"])
app.include_router(reports.router, prefix="/reports", tags=["reports"])


@app.get("/health")
def health():
    return {"status": "ok"}
