import asyncio
import tkinter as tk
from datetime import date
from dataclasses import dataclass

from tastytrade import DXLinkStreamer
from tastytrade.dxfeed import Greeks, Quote
from tastytrade import Session, Account
from tastytrade.instruments import Option, Equity

from lib import TTConfig, TTOption, TTOptionSide

from live_prices import LivePrices, TastytradeWrapper

from lib import TTConfig, TTApi, TTOption, TTOptionSide, TTOrder, TTTimeInForce, TTPriceEffect, TTOrderType, TTLegAction, TTInstrumentType


@dataclass
class Strategist:
    live_prices: LivePrices
    underlying_streamer_symbol: str
    options_interval: int
    reference_price: float
    options_streamer_symbols: list[str]
    put_to_sell: Option | None = None
    put_to_buy: Option | None = None
    call_to_sell: Option | None = None
    call_to_buy: Option | None = None
    put_to_sell_price = 0.0
    put_to_buy_price = 0.0
    call_to_sell_price = 0.0
    call_to_buy_price = 0.0
    # todo eliminate
    tt_api_put_to_sell: TTOption | None = None
    tt_api_put_to_buy: TTOption | None = None
    tt_api_call_to_sell: TTOption | None = None
    tt_api_call_to_buy: TTOption | None = None
    

    @classmethod
    async def create(
        cls,
        session: Session,
        underlying_symbol: str,
        options_interval: int
    ):
        streamer_symbols = await TastytradeWrapper.get_streamer_symbols_equities(session, [underlying_symbol])
        underlying_streamer_symbol = streamer_symbols[0]
        live_prices = await LivePrices.create(session, streamer_symbols)

        reference_price = (live_prices.quotes[underlying_streamer_symbol].bidPrice + live_prices.quotes[underlying_streamer_symbol].askPrice) / 2
        print(f'{underlying_streamer_symbol} is at {reference_price}')

        self = cls(live_prices, underlying_streamer_symbol, options_interval, reference_price, [])

        # Start the continuous build options loop
        asyncio.create_task(self.run_build_options_loop(session))
        self.api = TTApi()
        
        if not self.login_and_validate():
            print("Problem at login")
            return
        
        return self

    def login_and_validate(self):
        if not self.api.login():
            print("Login failed!")
            return False

        print("Login successful")

        if not self.api.validate():
            print("Validation failed!")
            return False

        print("Validation successful")

        if not self.api.fetch_accounts():
            print("Failed to fetch accounts!")
            return False

        print("Accounts fetched successfully")
        self.api.fetch_account_balance()
        return True
    
    @staticmethod
    def convert_option_to_tt_option(option):
        return TTOption(option.root_symbol, option.expiration_date.strftime('%y%m%d'), TTOptionSide.CALL if option.option_type == 'C' else TTOptionSide.PUT, int(option.strike_price))
        
    async def run_build_options_loop(self, session: Session, interval: int = 1):
        while True:
            await self._build_options_around(session)
            await asyncio.sleep(interval)

    async def _build_options_around(self, session: Session, search_interval: int = 50, price_threshold: float = 3.5, insurance_offset: int = 30):
        print(f'DEBUG: running updates')
        today = date.today()
        dd = str(today.day)
        mm = str(today.month)
        yy = str(today.year)[-2:]
        exp_date = yy + mm + dd
        nearest_strike_price = round(self.reference_price / self.options_interval) * self.options_interval
        lower_strikes = [strike for strike in range(nearest_strike_price, nearest_strike_price - search_interval, -self.options_interval)]
        higher_strikes = [strike for strike in range(nearest_strike_price, nearest_strike_price + search_interval, self.options_interval)]
        
        lower_tt_options = [TTOption('SPXW', exp_date, TTOptionSide.PUT, strike) for strike in lower_strikes]
        higher_tt_options = [TTOption('SPXW', exp_date, TTOptionSide.CALL, strike) for strike in higher_strikes]

        lower_options = await TastytradeWrapper.get_options(session, lower_tt_options)
        higher_options = await TastytradeWrapper.get_options(session, higher_tt_options)
        
        # Sorting and filtering logic
        lower_options.sort(key=lambda o: o.strike_price, reverse=True)
        higher_options.sort(key=lambda o: o.strike_price, reverse=False)

        lower_streamer_symbols = [o.streamer_symbol for o in lower_options]
        higher_streamer_symbols = [o.streamer_symbol for o in higher_options]

        await self.live_prices.add_symbols(lower_streamer_symbols + higher_streamer_symbols)

        # Logic for selecting put and call options based on the price threshold
        for option in lower_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            if price < price_threshold:
                self.put_to_sell = option
                insurance_strike_price = option.strike_price - insurance_offset
                tt_put_to_buy = TTOption('SPXW', exp_date, TTOptionSide.PUT, insurance_strike_price)
                self.put_to_buy = await Option.a_get_option(session, tt_put_to_buy.symbol)
                break

        for option in higher_options:
            price = self.live_prices.quotes[option.streamer_symbol].bidPrice
            if price < price_threshold:
                self.call_to_sell = option
                insurance_strike_price = option.strike_price + insurance_offset
                tt_call_to_buy = TTOption('SPXW', exp_date, TTOptionSide.CALL, insurance_strike_price)
                self.call_to_buy = await Option.a_get_option(session, tt_call_to_buy.symbol)
                break
            
        self.tt_api_put_to_sell = self.convert_option_to_tt_option(self.put_to_sell)
        self.tt_api_put_to_buy = self.convert_option_to_tt_option(self.put_to_buy)
        self.tt_api_call_to_sell = self.convert_option_to_tt_option(self.call_to_sell)
        self.tt_api_call_to_buy = self.convert_option_to_tt_option(self.call_to_buy)
        
        order = TTOrder(TTTimeInForce.GTC, 0.05, TTPriceEffect.DEBIT, TTOrderType.LIMIT)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, self.tt_api_put_to_sell.symbol, 1, TTLegAction.STO)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, self.tt_api_put_to_buy.symbol, 1, TTLegAction.BTO)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, self.tt_api_call_to_sell.symbol, 1, TTLegAction.STO)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, self.tt_api_call_to_buy.symbol, 1, TTLegAction.BTO)
        
        try:
            order_response = self.api.simple_order(order)
            margin_required = float(order_response["buying-power-effect"]["isolated-order-margin-requirement"])
            print(margin_required)
        except Exception as e:
            print(f"Error updating getting margin: {e}")
        
        await self.live_prices.add_symbols([self.call_to_buy.streamer_symbol, self.put_to_buy.streamer_symbol])

    def winnings(self):
        self.put_to_sell_price = self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice
        self.put_to_buy_price = self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice
        self.call_to_sell_price = self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice
        self.call_to_buy_price = self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice
        return (
            -self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice
            + self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice
            + self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice
            - self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice
        ) * 100

    def show_strategy(self):
        legs_are_available = self.put_to_buy is not None and \
            self.put_to_sell is not None and \
            self.call_to_sell is not None and \
            self.call_to_buy is not None
        leg_prices_are_available = legs_are_available and self.put_to_buy.streamer_symbol in self.live_prices.quotes and \
            self.put_to_sell.streamer_symbol in self.live_prices.quotes and \
            self.call_to_sell.streamer_symbol in self.live_prices.quotes and \
            self.call_to_buy.streamer_symbol in self.live_prices.quotes
        if not legs_are_available or not leg_prices_are_available:
            return None
        
        return self.winnings()


async def main():
    config = TTConfig()
    session = Session(config.username, config.password, is_test=not config.use_prod)
    strategist = await Strategist.create(session, 'SPX', 5)

    root = tk.Tk()
    root.title("Strategist Winnings")

    winnings_label = tk.Label(root, text="Calculating...", font=("Helvetica", 16))
    winnings_label.pack(pady=10)
    
    put_to_buy_label = tk.Label(root, text="Put to Buy: -", font=("Helvetica", 12))
    put_to_buy_label.pack()

    put_to_sell_label = tk.Label(root, text="Put to Sell: -", font=("Helvetica", 12))
    put_to_sell_label.pack()

    call_to_sell_label = tk.Label(root, text="Call to Sell: -", font=("Helvetica", 12))
    call_to_sell_label.pack()

    call_to_buy_label = tk.Label(root, text="Call to Buy: -", font=("Helvetica", 12))
    call_to_buy_label.pack()

    async def update_winnings():
        try:
            while True:
                winnings = strategist.show_strategy()
                if winnings is not None:
                    winnings_label.config(text=f"Winnings: ${winnings:.2f}")
                    if strategist.put_to_sell:
                        put_to_sell_label.config(
                            text=f"Put to Sell ({strategist.put_to_sell.symbol}): "
                                 f"${strategist.put_to_sell_price}"
                        )
                    if strategist.put_to_buy:
                        put_to_buy_label.config(
                            text=f"Put to Buy ({strategist.put_to_buy.symbol}): "
                                 f"${strategist.put_to_buy_price}"
                        )
                    if strategist.call_to_buy:
                        call_to_buy_label.config(
                            text=f"Call to Buy ({strategist.call_to_buy.symbol}): "
                                 f"${strategist.call_to_buy_price}"
                        )
                    if strategist.call_to_sell:
                        call_to_sell_label.config(
                            text=f"Call to Sell ({strategist.call_to_sell.symbol}): "
                                 f"${strategist.call_to_sell_price}"
                        )
                else:
                    winnings_label.config(text="Wait for strategy to initialize...")
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    async def tkinter_update():
        while True:
            root.update()
            await asyncio.sleep(0.1)

    # Start the update coroutines
    asyncio.create_task(update_winnings())
    await tkinter_update()

    await strategist.live_prices.close_channel()
    session.destroy()

if __name__ == '__main__':
    asyncio.run(main())
