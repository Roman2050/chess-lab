from enum import Enum


class StandardPerfType(str, Enum):
    ultraBullet = "ultraBullet"
    bullet = "bullet"
    blitz = "blitz"
    rapid = "rapid"
    classical = "classical"
    correspondence = "correspondence"
