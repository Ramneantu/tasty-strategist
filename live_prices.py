import asyncio
from dataclasses import dataclass

from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote
from tastytrade import Session
from tastytrade.instruments import Option, Equity

from lib import TTConfig, TTOption, TTOptionSide


class TastytradeWrapper:
    @classmethod
    async def get_streamer_symbols_options(cls, session: Session, tt_options: list[TTOption]):
        occ_symbols = [o.symbol for o in tt_options]
        options = await Option.a_get_options(session, occ_symbols)
        return [o.streamer_symbol for o in options]
    
    @classmethod
    async def get_options(cls, session: Session, tt_options: list[TTOption]):
        occ_symbols = [o.symbol for o in tt_options]
        return await Option.a_get_options(session, occ_symbols)
    
    @classmethod
    async def get_streamer_symbols_equities(cls, session: Session, occ_symbols: list[str]):
        equities = await Equity.a_get_equities(session, occ_symbols)
        return [e.streamer_symbol for e in equities]


@dataclass
class LivePrices:
    quotes: dict[str, Quote]
    streamer: DXLinkStreamer
    update_task: asyncio.Task | None
    streamer_symbols: list[Option]

    @classmethod
    async def create(
        cls,
        session: Session,
        streamer_symbols: list[str],
    ):
        streamer = await DXLinkStreamer.create(session)
        # subscribe to quotes and greeks for all options on that date
        await streamer.subscribe(Quote, streamer_symbols)

        self = cls({}, streamer, None, streamer_symbols)

        self.update_task = asyncio.create_task(self._update_quotes())

        # wait we have quotes and greeks for each option
        while len(self.quotes) != len(streamer_symbols):
            await asyncio.sleep(0.1)

        return self

    async def _update_quotes(self):
        try:
            async for e in self.streamer.listen(Quote):
                self.quotes[e.eventSymbol] = e
        except asyncio.CancelledError:
            await self.streamer.unsubscribe_all(Quote)
            await self.streamer.close()
            print('Unsubscribed from qoutes')
            raise asyncio.CancelledError
        
    async def add_symbols(self, streamer_symbols: list[str]):
        new_streamer_symbols = list(set(streamer_symbols) - set(self.streamer_symbols))
        await self.streamer.subscribe(Quote, new_streamer_symbols)
        self.streamer_symbols += new_streamer_symbols
        while len(self.quotes) != len(self.streamer_symbols):
            await asyncio.sleep(0.1)
        # print(f'Successfully added the symbols {new_streamer_symbols}')
        
    async def close_channel(self):
        self.update_task.cancel()
        try:
            await self.update_task
        except asyncio.CancelledError:
            print('Closed update task')


async def main():
    config = TTConfig()
    session = Session(config.username, config.password, is_test=not config.use_prod)
    
    options = [
        TTOption('SPXW', '241025', TTOptionSide.CALL, 5750),
        TTOption('SPXW', '241025', TTOptionSide.CALL, 5850),
        TTOption('SPXW', '241025', TTOptionSide.CALL, 5950),
        TTOption('SPXW', '241025', TTOptionSide.CALL, 6050),
    ]
    equity_symbols_occ = ['SPX', 'AAPL']

    option_symbols_streamer = await TastytradeWrapper.get_streamer_symbols_options(session, options)
    equity_symbols_streamer = await TastytradeWrapper.get_streamer_symbols_equities(session, equity_symbols_occ)
    streamer_symbols = option_symbols_streamer + equity_symbols_streamer

    print(f'Options: {option_symbols_streamer}')
    print(f'Equities: {equity_symbols_streamer}')

    live_price_streamer = await LivePrices.create(session, option_symbols_streamer)
    
    for _ in range(5):
        for symbol in live_price_streamer.streamer_symbols:
            print(f'Price of {symbol} is {live_price_streamer.quotes[symbol].askPrice}')
        print('==============================')
        await asyncio.sleep(2)

    await live_price_streamer.add_symbols(equity_symbols_streamer)
    for _ in range(5):
        for symbol in live_price_streamer.streamer_symbols:
            print(f'Price of {symbol} is {live_price_streamer.quotes[symbol].askPrice}')
        print('==============================')
        await asyncio.sleep(2)

    await live_price_streamer.close_channel()
        
    session.destroy()


if __name__ == '__main__':
    asyncio.run(main())
