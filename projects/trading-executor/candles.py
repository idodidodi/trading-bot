from dataclasses import dataclass

@dataclass(frozen=True)
class Candle:
    start:int
    end:int
    open:float
    high:float
    low:float
    close:float
