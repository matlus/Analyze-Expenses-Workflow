from dataclasses import dataclass
from enum import StrEnum


class ExpenseCategory(StrEnum):
    HOUSING = "housing"
    BILLS_UTILITIES = "bills_utilities"
    GROCERIES = "groceries"
    DINING_COFFEE = "dining_coffee"
    TRANSPORTATION = "transportation"
    TRAVEL = "travel"
    HEALTH_FITNESS = "health_fitness"
    INSURANCE = "insurance"
    EDUCATION_CHILDCARE = "education_childcare"
    SHOPPING = "shopping"
    PERSONAL_CARE = "personal_care"
    ENTERTAINMENT = "entertainment"
    SUBSCRIPTIONS = "subscriptions"
    PETS = "pets"
    GIFTS_DONATIONS = "gifts_donations"
    TAXES_GOVERNMENT_FEES = "taxes_government_fees"
    FINANCIAL_FEES_INTEREST = "financial_fees_interest"
    WORK_EXPENSES = "work_expenses"
    OTHER = "other"
    UNCATEGORIZED = "uncategorized"


@dataclass(frozen=True, slots=True)
class ExpenseCategoryDefinition:
    category: ExpenseCategory
    criterion: str


@dataclass(frozen=True, slots=True)
class KnownExpenseMerchant:
    name: str
    expense_category: ExpenseCategory


@dataclass(frozen=True, slots=True)
class ExpenseCategoryCatalog:
    definitions: tuple[ExpenseCategoryDefinition, ...]
    mixed_retailer_merchants: tuple[str, ...]
    known_merchants: tuple[KnownExpenseMerchant, ...]


STANDARD_EXPENSE_CATEGORY_CATALOG = ExpenseCategoryCatalog(
    definitions=(
        ExpenseCategoryDefinition(ExpenseCategory.HOUSING, "Rent, mortgage or home loan payments, property maintenance, and home repairs"),
        ExpenseCategoryDefinition(ExpenseCategory.BILLS_UTILITIES, "Electricity, gas, water, telephone, wireless, internet, and other utility bills"),
        ExpenseCategoryDefinition(
            ExpenseCategory.GROCERIES, "Food and beverages bought for consumption at home when the purchase is identifiable as groceries"
        ),
        ExpenseCategoryDefinition(ExpenseCategory.DINING_COFFEE, "Prepared meals, takeout, cafes, coffee, and drinks at restaurants or bars"),
        ExpenseCategoryDefinition(
            ExpenseCategory.TRANSPORTATION, "Local transit, ride services, fuel, parking, vehicle repairs, and car loan or lease payments"
        ),
        ExpenseCategoryDefinition(
            ExpenseCategory.TRAVEL, "Flights, lodging, rental cars, airport parking, and other expenses explicitly connected to a trip"
        ),
        ExpenseCategoryDefinition(
            ExpenseCategory.HEALTH_FITNESS, "Medical, dental, pharmacy, therapy, gym, and fitness charges other than digital subscriptions"
        ),
        ExpenseCategoryDefinition(ExpenseCategory.INSURANCE, "Insurance premiums when identifiable separately from a mortgage or car payment"),
        ExpenseCategoryDefinition(ExpenseCategory.EDUCATION_CHILDCARE, "Tuition, school materials, courses, and childcare"),
        ExpenseCategoryDefinition(
            ExpenseCategory.SHOPPING, "Identifiable retail goods such as clothing, electronics, books, sporting goods, and home goods"
        ),
        ExpenseCategoryDefinition(ExpenseCategory.PERSONAL_CARE, "Haircuts, grooming, dry cleaning, and personal care services"),
        ExpenseCategoryDefinition(ExpenseCategory.ENTERTAINMENT, "Movies, games, concerts, event tickets, and recreation"),
        ExpenseCategoryDefinition(ExpenseCategory.SUBSCRIPTIONS, "Recurring digital services, media subscriptions, and app or club memberships"),
        ExpenseCategoryDefinition(ExpenseCategory.PETS, "Pet food, veterinary care, grooming, and pet supplies"),
        ExpenseCategoryDefinition(ExpenseCategory.GIFTS_DONATIONS, "Gifts and charitable or religious donations"),
        ExpenseCategoryDefinition(ExpenseCategory.TAXES_GOVERNMENT_FEES, "Taxes and government charges not already included in a combined payment"),
        ExpenseCategoryDefinition(
            ExpenseCategory.FINANCIAL_FEES_INTEREST, "Bank charges, loan interest, and financial service fees stated separately"
        ),
        ExpenseCategoryDefinition(ExpenseCategory.WORK_EXPENSES, "Expenses explicitly described as job or business related"),
        ExpenseCategoryDefinition(
            ExpenseCategory.OTHER,
            "Known spending with an unknown purchase type, including merchant-only Target, Amazon, and Costco charges",
        ),
        ExpenseCategoryDefinition(
            ExpenseCategory.UNCATEGORIZED,
            "The line does not establish whether or why money was spent; includes bank transfers and cash withdrawals without a stated purchase",
        ),
    ),
    mixed_retailer_merchants=("Target", "Amazon", "Costco"),
    known_merchants=(
        KnownExpenseMerchant("Shell", ExpenseCategory.TRANSPORTATION),
        KnownExpenseMerchant("Steam", ExpenseCategory.ENTERTAINMENT),
        KnownExpenseMerchant("Best Buy", ExpenseCategory.SHOPPING),
        KnownExpenseMerchant("Home Depot", ExpenseCategory.SHOPPING),
        KnownExpenseMerchant("REI", ExpenseCategory.SHOPPING),
        KnownExpenseMerchant("Barnes & Noble", ExpenseCategory.ENTERTAINMENT),
        KnownExpenseMerchant("Walgreens", ExpenseCategory.HEALTH_FITNESS),
        KnownExpenseMerchant("Thai Orchid", ExpenseCategory.DINING_COFFEE),
        KnownExpenseMerchant("IKEA", ExpenseCategory.SHOPPING),
        KnownExpenseMerchant("Kroger", ExpenseCategory.GROCERIES),
        KnownExpenseMerchant("Aldi", ExpenseCategory.GROCERIES),
    ),
)
