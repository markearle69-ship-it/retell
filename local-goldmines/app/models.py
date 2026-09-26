from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .db import Base


class Project(Base):
    """One piece of research: a main niche (e.g. "electrician") around a hub city."""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    niche = Column(String, nullable=False)
    country = Column(String, nullable=False)  # "GB" or "US"
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    areas = relationship(
        "Area", back_populates="project", cascade="all, delete-orphan", order_by="Area.id"
    )

    @property
    def hub(self):
        return next((a for a in self.areas if a.parent_id is None), None)


class Area(Base):
    """A centre point we search around.

    The hub city is level 0 (default 10 mile radius). Drilling down into a
    chosen town creates a level-1 Area centred on it (default 5 miles), whose
    picks become that town-site's mini suburbs.
    """

    __tablename__ = "areas"

    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("areas.id"), nullable=True)
    level = Column(Integer, nullable=False, default=0)
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    radius_miles = Column(Float, nullable=False)
    min_population = Column(Integer, nullable=False, default=0)
    researched_at = Column(DateTime, nullable=True)
    research_error = Column(String, nullable=True)

    project = relationship("Project", back_populates="areas")
    parent = relationship("Area", remote_side=[id], back_populates="children")
    children = relationship("Area", back_populates="parent", cascade="all, delete-orphan")
    candidates = relationship(
        "Candidate", back_populates="area", cascade="all, delete-orphan", order_by="Candidate.id"
    )

    @property
    def selected(self):
        return [c for c in self.candidates if c.selected]


class Candidate(Base):
    """A town/suburb found inside an Area's radius, with the stats used to pick it."""

    __tablename__ = "candidates"

    id = Column(Integer, primary_key=True)
    area_id = Column(Integer, ForeignKey("areas.id"), nullable=False)
    name = Column(String, nullable=False)
    place_type = Column(String, nullable=True)
    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    distance_miles = Column(Float, nullable=False)
    bearing = Column(Float, nullable=False)
    sector = Column(String, nullable=False)
    population = Column(Integer, nullable=True)
    population_source = Column(String, nullable=True)
    income = Column(Integer, nullable=True)  # UK: avg household income; US: median household income
    house_price = Column(Integer, nullable=True)  # UK: median price paid; US: median home value
    affluence_score = Column(Float, nullable=True)  # 0-100, relative to the other candidates
    selected = Column(Boolean, nullable=False, default=False)

    area = relationship("Area", back_populates="candidates")

    def features(self) -> dict:
        return {
            "name": self.name,
            "place_type": self.place_type,
            "distance_miles": self.distance_miles,
            "sector": self.sector,
            "population": self.population,
            "income": self.income,
            "house_price": self.house_price,
            "affluence_score": self.affluence_score,
        }


class SelectionLog(Base):
    """Every save of your picks, with the full candidate list and what each looked like.

    This is the training data for the v2 auto-picker: for each area, which
    candidates were on offer, their stats at the time, and which you chose.
    """

    __tablename__ = "selection_logs"

    id = Column(Integer, primary_key=True)
    area_id = Column(Integer, ForeignKey("areas.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    context = Column(JSON, nullable=False)  # niche, country, level, radius, centre
    candidates = Column(JSON, nullable=False)  # [{...features, "selected": bool}]
