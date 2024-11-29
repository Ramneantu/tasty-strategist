import asyncio
from dataclasses import dataclass

from tastytrade import AlertStreamer, Session, Account
from tastytrade.order import NewOrder, OrderAction, OrderTimeInForce, OrderType, PlacedOrderResponse, PlacedOrder

@dataclass
class AccountUpdates:
    streamer: AlertStreamer
    orders: dict[int, PlacedOrder]
    update_task: asyncio.Task | None = None

    @classmethod
    async def create(
        cls,
        session: Session,
        account: Account
    ):
        streamer = await AlertStreamer.create(session)
        await streamer.subscribe_accounts([account])

        self = cls(streamer, {})

        self.update_task = asyncio.Task(self._update_orders())

        return self

    
    async def _update_orders(self):
        try:
            async for e in self.streamer.listen(PlacedOrder):
                self.orders[e.id] = e
        except asyncio.CancelledError:
            await self.streamer.close()
            raise asyncio.CancelledError
        
    async def close_channel(self):
        self.update_task.cancel()
        try:
            await self.update_task
        except asyncio.CancelledError:
            print('Closed account update task')
