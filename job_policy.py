"""Destination rules shared by packet pickup and delivery routing."""
from dataclasses import dataclass
import re
from typing import Self

from job_types import JobCandidate, JobDescription
from player import PlayerSettings


@dataclass(frozen=True)
class JobPolicy:
    destinations: frozenset[str]
    minimum: int = 0
    maximum: int = 0

    @classmethod
    def from_settings(cls, settings: PlayerSettings | None, low_job: bool, avoid_pit: bool) -> Self:
        selected = getattr(settings, 'job_destinations', 'auto')
        if selected == 'auto':
            destinations = {'uranus'} if low_job else {'charon', 'monster_pit'}
        else:
            destinations = {part.strip() for part in selected.split(',')}
        if avoid_pit:
            destinations.discard('monster_pit')
        if not destinations:
            raise ValueError('Job destination settings exclude all jobs (check avoid_pit)')
        return cls(frozenset(destinations), getattr(settings, 'job_min_level', 0),
                   getattr(settings, 'job_max_level', 0))

    def candidate(self, job: JobCandidate) -> bool:
        return (job['type'] == 2 and job['title'] in ('Launch a Satellite', 'Dispose Galactic Garbage')
                and job['level'] >= self.minimum and (not self.maximum or job['level'] <= self.maximum))

    def matches(self, description: JobDescription) -> bool:
        if not description.get('available') or description.get('title') not in ('Launch a Satellite', 'Dispose Galactic Garbage'):
            return False
        match = re.search(r':\s*-(.*?)-\s+in\s+(.+?)\s+using\b', description.get('description', ''), re.I)
        if not match:
            return False
        target, sector = (' '.join(value.casefold().split()) for value in match.groups())
        if target in {'ariel', 'puck'} and sector == 'uranus':
            destination = 'uranus'
        elif target == 'charon monster pit' and sector == 'pluto and charon':
            destination = 'monster_pit'
        elif target == 'charon' and sector == 'pluto and charon':
            destination = 'charon'
        else:
            return False
        return destination in self.destinations
