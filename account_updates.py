import asyncio
from dataclasses import dataclass
from decimal import Decimal

from tastytrade import AlertStreamer, Session, Account
from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType, PlacedOrderResponse, PlacedOrder
from tastytrade.account import CurrentPosition

@dataclass
class AccountUpdates:
    streamer: AlertStreamer
    orders: dict[int, PlacedOrder]
    positions: dict[str, CurrentPosition]
    update_orders_task: asyncio.Task | None = None
    update_positions_task: asyncio.Task | None = None

    @classmethod
    async def create(
        cls,
        session: Session,
        account: Account
    ):
        streamer = await AlertStreamer.create(session)
        await streamer.subscribe_accounts([account])

        self = cls(streamer, {}, {})

        self.update_orders_task = asyncio.Task(self._update_orders())
        self.update_positions_task = asyncio.Task(self._update_positions())

        return self

    def num_open_positions(self):
        num = 0
        # May need to filter this on the underlying symbol
        for symbol, position in self.positions.items():
            if position.quantity > Decimal('0.0'):
                num += 1
        return num
    
    async def _update_orders(self):
        try:
            async for e in self.streamer.listen(PlacedOrder):
                self.orders[e.id] = e
        except asyncio.CancelledError:
            # Maybe we need some cleanup here?
            raise asyncio.CancelledError
        
    async def _update_positions(self):
        try:
            async for e in self.streamer.listen(CurrentPosition):
                self.positions[e.symbol] = e
        except asyncio.CancelledError:
            # Maybe we need some cleanup here?
            raise asyncio.CancelledError
        
    async def close_channel(self):
        self.update_orders_task.cancel()
        self.update_positions_task.cancel()
        try:
            await self.update_orders_task
            await self.update_positions_task
            await self.streamer.close()
        except asyncio.CancelledError:
            print('Closed account update task')
