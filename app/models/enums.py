from enum import Enum

class StandardPerType(str, Enum):
    ultraBullet = "ultraBullet"
    bullet = "bullet"
    blitz = "blitz"
    rapid = "rapid"
    classical = "classical"
    correspondence = "correspondence"