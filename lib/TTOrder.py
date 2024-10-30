import json
from enum import Enum
from tastytrade.instruments import Option

class TTOrderType(Enum):
  LIMIT = 'Limit'
  MARKET = 'Market'

class TTPriceEffect(Enum):
  CREDIT = 'Credit'
  DEBIT = 'Debit'

class TTOrderStats(Enum):
  RECEIVED = 'Received'
  CANCELLED = 'Cancelled'
  FILLED = 'Filled'
  EXPIRED = 'Expired'
  LIVE = 'Live'
  REJECTED = 'Rejected'

class TTTimeInForce(Enum):
  DAY = 'Day'
  GTC = 'GTC'
  GTD = 'GTD'

class TTInstrumentType(Enum):
  EQUITY = 'Equity'
  EQUITY_OPTION = 'Equity Option'
  FUTURE = 'Future'
  FUTURE_OPTION = 'Future Option'
  NOTIONAL_MARKET = 'Notional Market'

class TTLegAction(Enum):
  STO = 'Sell to Open'
  STC = 'Sell to Close'
  BTO = 'Buy to Open'
  BTC = 'Buy to Close'

class TTOptionSide(Enum):
  PUT = 'P'
  CALL = 'C'

class TTOption:
  symbol: str = None
  streamer_symbol: str = None
  strike_price: str = None

  def __init__(self, symbol: str = None, date: str = None,
                side: TTOptionSide = None, strike: float = None) -> None:
    symbol = symbol.ljust(6, ' ')
    strike_str = str(int(strike * 100)).replace('.', '').zfill(6)
    self.symbol = symbol + date + side.value + '0' + strike_str + '0'
    self.strike_price = int(round(strike)) # integer strike
    self.streamer_symbol = Option.occ_to_streamer_symbol(self.symbol)

class TTOrder:
    def __init__(self, tif: TTTimeInForce = None, price: float = None,
                 price_effect: TTPriceEffect = None, order_type: TTOrderType = None) -> None:
        self.tif = tif if tif else TTTimeInForce.GTC
        self.order_type = order_type if order_type else TTOrderType.LIMIT
        self.price = '{:.2f}'.format(price) if price else "0.00"
        self.price_effect = price_effect if price_effect else TTPriceEffect.CREDIT
        
        # Initialize legs and body as instance-level attributes
        self.legs = []
        self.body = {}

    def add_leg(self, instrument_type: TTInstrumentType = None,
                symbol: str = None, quantity: int = 0,
                action: TTLegAction = None) -> None:
        if len(self.legs) >= 4:
            print(f'Error: cannot have more than 4 legs per order.')
            return

        if not all([instrument_type, symbol, quantity, action]):
            print('Invalid parameters provided for add_leg.')
            return

        # Add a new leg to the order's instance-specific legs list
        self.legs.append({
            'instrument-type': instrument_type.value,
            'symbol': symbol,
            'quantity': quantity,
            'action': action.value
        })

    def build_order(self) -> dict:
        # Build the body using the current instance's attributes
        self.body = {
            'time-in-force': self.tif.value,
            'price': self.price,
            'price-effect': self.price_effect.value,
            'order-type': self.order_type.value,
            'legs': self.legs
        }
        return self.body

