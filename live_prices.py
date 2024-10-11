import asyncio
from datetime import date
from dataclasses import dataclass
from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote, EventType
from tastytrade import Option, Session, Account
from lib import TTConfig, TTOption, TTOptionSide

@dataclass
class LivePrices:
    quotes: dict[str, Quote]
    streamer: DXLinkStreamer
    update_task: asyncio.Task | None
    options: list[Option]

    @classmethod
    async def create(
        cls,
        session: Session,
        symbols: list[str],
    ):
        
        options = [Option.get_option(session, symbol) for symbol in symbols]
        # the `streamer_symbol` property is the symbol used by the streamer
        streamer_symbols = [o.streamer_symbol for o in options]

        streamer = await DXLinkStreamer.create(session)
        # subscribe to quotes and greeks for all options on that date
        await streamer.subscribe(EventType.QUOTE, streamer_symbols)

        self = cls({}, streamer, None, options)

        self.update_task = asyncio.create_task(self._update_quotes())

        # wait we have quotes and greeks for each option
        while len(self.quotes) != len(options):
            await asyncio.sleep(0.1)

        return self

    async def _update_quotes(self):
        try:
            async for e in self.streamer.listen(EventType.QUOTE):
                self.quotes[e.eventSymbol] = e
        except asyncio.CancelledError:
            print('Cancelling update task...')
            await self.streamer.cancel_channel(EventType.QUOTE)
            await self.streamer.close()
            print('Channels were closed')
            raise asyncio.CancelledError
        
    async def close_channel(self):
        self.update_task.cancel()
        try:
            await self.update_task
        except asyncio.CancelledError:
            print('Task was successfully canceled')


async def main():
    config = TTConfig()
    session = Session(config.username, config.password, is_test=not config.use_prod)
    
    options = [
        TTOption('SPXW', '241011', TTOptionSide.CALL, 5650),
        TTOption('SPXW', '241011', TTOptionSide.CALL, 5750),
        TTOption('SPXW', '241011', TTOptionSide.CALL, 5850),
        TTOption('SPXW', '241011', TTOptionSide.CALL, 5950)
    ]
    symbols = [option.symbol for option in options]

    print(symbols)

    live_price_streamer = await LivePrices.create(session, symbols)
    streamer_symbols = live_price_streamer.quotes.keys()
    for _ in range(10):
        for symbol in streamer_symbols:
            print(f'Price of {symbol} is {live_price_streamer.quotes[symbol].askPrice}')
        print('==============================')
        await asyncio.sleep(1)
        print(f'Event queue size: {live_price_streamer.streamer._queues[EventType.QUOTE].qsize()}') # Nu mai actualizeaza aici. Nu inteleg de ce...
        print('==============================')

    await live_price_streamer.close_channel()
        
    session.destroy()


if __name__ == '__main__':
    asyncio.run(main())