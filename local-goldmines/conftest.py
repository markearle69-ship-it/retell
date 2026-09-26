import os

# Must be set before app modules import settings / create the engine.
os.environ["DATABASE_URL"] = "sqlite:///./test_goldmines.db"
os.environ["APP_PASSWORD"] = "test-password"
os.environ["CENSUS_API_KEY"] = "test-census-key"
