from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from functools import total_ordering


from tastytrade.instruments import Option
from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType

@dataclass
class IronCondor:
    insurance_put: Option
    main_put: Option
    main_call: Option
    insurance_call: Option

    def __init__(self, insurance_put: Option, main_put: Option, main_call: Option, insurance_call: Option):
        # Validate inputs
        if None in (insurance_put, main_put, main_call, insurance_call):
            raise ValueError("IronCondor cannot be initialized with None option objects.")

        # Initialize the instance variables
        self.insurance_put = insurance_put
        self.main_put = main_put
        self.main_call = main_call
        self.insurance_call = insurance_call

    def _order(self, open: bool, limit: Decimal) -> NewOrder:
        # Negative decimal to close position
        leg_put_buy = self.insurance_put.build_leg(Decimal(1), OrderAction.BUY_TO_OPEN if open else OrderAction.SELL_TO_CLOSE)
        leg_put_sell = self.main_put.build_leg(Decimal(1), OrderAction.SELL_TO_OPEN if open else OrderAction.BUY_TO_CLOSE)
        leg_call_sell = self.main_call.build_leg(Decimal(1), OrderAction.SELL_TO_OPEN if open else OrderAction.BUY_TO_CLOSE)
        leg_call_buy = self.insurance_call.build_leg(Decimal(1), OrderAction.BUY_TO_OPEN if open else OrderAction.SELL_TO_CLOSE)

        return NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[leg_put_buy, leg_put_sell, leg_call_sell, leg_call_buy],  # you can have multiple legs in an order
            price=limit  # limit price, $10/share debit for a total value of $50
        )
    
    # Opening Iron Condor gives money
    def opening_order(self, limit: Decimal = Decimal('0.05')):
        return self._order(True, limit)
    
    # Closing Iron Condor consts money
    def closing_order(self, limit: Decimal = Decimal('-0.05')):
        return self._order(False, limit)
    

@total_ordering
class PositionState(Enum):
    NO_POSITION = 0
    PENDING = 1
    OPENING_REQUESTED = 2
    OPEN = 3
    CLOSING_REQUESTED = 4
    CLOSED = 5
    # Comparator for position state based on how far along they are
    def __lt__(self, other):
        if self.__class__ is other.__class__:
            return self.value < other.value
        return NotImplemented