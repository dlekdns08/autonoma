"""Day/night cycle, seasons, weather.

Separated from the larger world monolith because these classes have no
inbound dependencies on agent state — they only push into it via
``WorldClock.get_mood_modifier`` / ``get_xp_modifier`` which take no
arguments.
"""

from __future__ import annotations

import random
from enum import Enum

from autonoma.world.personality import Mood


class TimeOfDay(str, Enum):
    DAWN = "dawn"
    MORNING = "morning"
    AFTERNOON = "afternoon"
    EVENING = "evening"
    NIGHT = "night"


class Season(str, Enum):
    SPRING = "spring"
    SUMMER = "summer"
    AUTUMN = "autumn"
    WINTER = "winter"


class Weather(str, Enum):
    SUNNY = "sunny"
    CLOUDY = "cloudy"
    RAINY = "rainy"
    STORMY = "stormy"
    SNOWY = "snowy"
    WINDY = "windy"
    STARRY = "starry"
    FOGGY = "foggy"


TIME_EMOJIS = {
    TimeOfDay.DAWN: "🌅",
    TimeOfDay.MORNING: "☀",
    TimeOfDay.AFTERNOON: "🌤",
    TimeOfDay.EVENING: "🌇",
    TimeOfDay.NIGHT: "🌙",
}

WEATHER_EMOJIS = {
    Weather.SUNNY: "☀",
    Weather.CLOUDY: "☁",
    Weather.RAINY: "🌧",
    Weather.STORMY: "⛈",
    Weather.SNOWY: "❄",
    Weather.WINDY: "🍃",
    Weather.STARRY: "✦",
    Weather.FOGGY: "🌫",
}

SEASON_EMOJIS = {
    Season.SPRING: "🌸",
    Season.SUMMER: "🌻",
    Season.AUTUMN: "🍂",
    Season.WINTER: "❄",
}

# Weather probabilities per season
SEASON_WEATHER: dict[Season, list[tuple[Weather, float]]] = {
    Season.SPRING: [
        (Weather.SUNNY, 0.3), (Weather.CLOUDY, 0.2), (Weather.RAINY, 0.3),
        (Weather.WINDY, 0.15), (Weather.FOGGY, 0.05),
    ],
    Season.SUMMER: [
        (Weather.SUNNY, 0.5), (Weather.CLOUDY, 0.15), (Weather.STORMY, 0.15),
        (Weather.WINDY, 0.1), (Weather.RAINY, 0.1),
    ],
    Season.AUTUMN: [
        (Weather.CLOUDY, 0.3), (Weather.RAINY, 0.25), (Weather.WINDY, 0.2),
        (Weather.FOGGY, 0.15), (Weather.SUNNY, 0.1),
    ],
    Season.WINTER: [
        (Weather.SNOWY, 0.35), (Weather.CLOUDY, 0.2), (Weather.STORMY, 0.15),
        (Weather.WINDY, 0.15), (Weather.SUNNY, 0.1), (Weather.FOGGY, 0.05),
    ],
}


class WorldClock:
    """Tracks the passage of time in the agent world."""

    TIMES = [TimeOfDay.DAWN, TimeOfDay.MORNING, TimeOfDay.AFTERNOON, TimeOfDay.EVENING, TimeOfDay.NIGHT]
    SEASONS = [Season.SPRING, Season.SUMMER, Season.AUTUMN, Season.WINTER]

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)
        self.time_of_day: TimeOfDay = TimeOfDay.MORNING
        self.season: Season = Season.SPRING
        self.weather: Weather = Weather.SUNNY
        self.day: int = 1

    def tick(self, round_number: int) -> dict[str, str]:
        """Advance the clock. Returns dict of what changed."""
        changes: dict[str, str] = {}

        # Time advances every round (5 time slots per day)
        time_idx = round_number % len(self.TIMES)
        new_time = self.TIMES[time_idx]
        if new_time != self.time_of_day:
            changes["time"] = new_time.value
            self.time_of_day = new_time

        # New day every 5 rounds
        new_day = (round_number // len(self.TIMES)) + 1
        if new_day != self.day:
            self.day = new_day
            changes["day"] = str(self.day)
            # Weather changes each day
            self._roll_weather()
            changes["weather"] = self.weather.value

        # Season changes every 20 rounds (4 days per season)
        season_idx = ((round_number - 1) // 20) % len(self.SEASONS)
        new_season = self.SEASONS[season_idx]
        if new_season != self.season:
            self.season = new_season
            changes["season"] = new_season.value
            self._roll_weather()
            changes["weather"] = self.weather.value

        return changes

    def _roll_weather(self) -> None:
        """Roll weather based on season probabilities."""
        weather_table = SEASON_WEATHER[self.season]
        roll = self._rng.random()
        cumulative = 0.0
        for weather, prob in weather_table:
            cumulative += prob
            if roll <= cumulative:
                self.weather = weather
                return
        self.weather = weather_table[0][0]

    @property
    def is_night(self) -> bool:
        return self.time_of_day in (TimeOfDay.NIGHT, TimeOfDay.EVENING)

    @property
    def sky_line(self) -> str:
        """Kawaii sky description for TUI."""
        t_emoji = TIME_EMOJIS[self.time_of_day]
        w_emoji = WEATHER_EMOJIS[self.weather]
        s_emoji = SEASON_EMOJIS[self.season]
        return f"{t_emoji} {self.time_of_day.value} {w_emoji} {self.weather.value} {s_emoji} {self.season.value} — Day {self.day}"

    def get_mood_modifier(self) -> Mood | None:
        """Some weather/time combos affect agent mood."""
        if self.weather == Weather.STORMY:
            return Mood.WORRIED
        if self.weather == Weather.SUNNY and self.time_of_day == TimeOfDay.MORNING:
            return Mood.HAPPY
        if self.time_of_day == TimeOfDay.NIGHT:
            return Mood.RELAXED
        if self.weather == Weather.SNOWY:
            return Mood.CURIOUS
        return None

    def get_xp_modifier(self) -> float:
        """Weather can affect XP gains."""
        if self.weather == Weather.SUNNY:
            return 1.1  # +10%
        if self.weather == Weather.STORMY:
            return 0.9  # -10%
        if self.time_of_day == TimeOfDay.NIGHT:
            return 1.15  # Night owl bonus
        return 1.0
