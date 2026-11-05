"""Public country/state picklists for registration and profile editing."""
from fastapi import APIRouter, HTTPException
from app.utils.geography import countries_list, country_metadata, states_for

router = APIRouter(prefix="/locations", tags=["Locations"])


@router.get("/countries")
def list_countries():
    return countries_list()


@router.get("/countries/{country_code}/states")
def list_states(country_code: str):
    if not country_metadata(country_code):
        raise HTTPException(404, "Country not found or no supported currency")
    return states_for(country_code)
