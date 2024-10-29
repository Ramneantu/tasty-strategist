import tkinter as tk
import asyncio
import threading
from datetime import date, timedelta
from dataclasses import dataclass

from tastytrade import Session
from tastytrade.instruments import Option
from lib import TTConfig, TTOption, TTOptionSide
from live_prices import LivePrices, TastytradeWrapper

@dataclass
class Strategist:
    live_prices: LivePrices
    underlying_streamer_symbol: str
    options_interval: int
    reference_price: float
    put_to_sell: Option = None
    put_to_buy: Option = None
    call_to_sell: Option = None
    call_to_buy: Option = None

    @classmethod
    async def create(
        cls,
        session: Session,
        underlying_symbol: str,
        options_interval: int
    ):
        print("Tick create")
        streamer_symbols = await TastytradeWrapper.get_streamer_symbols_equities(session, [underlying_symbol])
        underlying_streamer_symbol = streamer_symbols[0]
        live_prices = await LivePrices.create(session, streamer_symbols)

        reference_price = (live_prices.quotes[underlying_streamer_symbol].bidPrice + live_prices.quotes[underlying_streamer_symbol].askPrice) / 2
        print(f'{underlying_streamer_symbol} is at {reference_price}')

        self = cls(live_prices, underlying_streamer_symbol, options_interval, reference_price)
        await self._build_options_around(session)
        return self

    async def _build_options_around(self, session: Session, search_interval: int = 50, price_threshold: float = 3.5, insurance_offset: int = 30):
        """Builds the options strategy based on the reference price and specified criteria."""
        print("Tick _build_options_around")
        
        # Proceed with options setup without premature checks on variables that are about to be initialized.
        today = date.today() + timedelta(days=1)
        exp_date = f"{str(today.year)[-2:]}{today.month:02d}{today.day:02d}"
        nearest_strike_price = round(self.reference_price / self.options_interval) * self.options_interval

        # Define strike ranges for options to buy and sell
        lower_strikes = [strike for strike in range(nearest_strike_price, nearest_strike_price - search_interval, -self.options_interval)]
        higher_strikes = [strike for strike in range(nearest_strike_price, nearest_strike_price + search_interval, self.options_interval)]

        lower_tt_options = [TTOption('SPXW', exp_date, TTOptionSide.PUT, strike) for strike in lower_strikes]
        higher_tt_options = [TTOption('SPXW', exp_date, TTOptionSide.CALL, strike) for strike in higher_strikes]

        # Fetch options data
        lower_options = await TastytradeWrapper.get_options(session, lower_tt_options)
        higher_options = await TastytradeWrapper.get_options(session, higher_tt_options)

        lower_options.sort(key=lambda o: o.strike_price, reverse=True)
        higher_options.sort(key=lambda o: o.strike_price)

        # Initialize PUT options
        for option in lower_options:
            try:
                price = self.live_prices.quotes[option.streamer_symbol].bidPrice
                if price < price_threshold:
                    self.put_to_sell = option
                    insurance_strike_price = option.strike_price - insurance_offset
                    tt_put_to_buy = TTOption('SPXW', exp_date, TTOptionSide.PUT, insurance_strike_price)
                    self.put_to_buy = await Option.a_get_option(session, tt_put_to_buy.symbol)
                    break
            except KeyError:
                print(f"Price not available for PUT option at strike {option.strike_price}")
                continue

        # Initialize CALL options
        for option in higher_options:
            try:
                price = self.live_prices.quotes[option.streamer_symbol].bidPrice
                if price < price_threshold:
                    self.call_to_sell = option
                    insurance_strike_price = option.strike_price + insurance_offset
                    tt_call_to_buy = TTOption('SPXW', exp_date, TTOptionSide.CALL, insurance_strike_price)
                    self.call_to_buy = await Option.a_get_option(session, tt_call_to_buy.symbol)
                    break
            except KeyError:
                print(f"Price not available for CALL option at strike {option.strike_price}")
                continue

        # Final check to confirm all required options are initialized
        if all([self.put_to_sell, self.put_to_buy, self.call_to_sell, self.call_to_buy]):
            await self.live_prices.add_symbols([
                self.put_to_sell.streamer_symbol,
                self.put_to_buy.streamer_symbol,
                self.call_to_sell.streamer_symbol,
                self.call_to_buy.streamer_symbol
            ])
        else:
            print("Not all options were initialized correctly; exiting _build_options_around.")


    def winnings(self):
        """Calculates the total winnings for the strategy based on live prices."""
        return (
            -self.live_prices.quotes[self.put_to_buy.streamer_symbol].askPrice +
            self.live_prices.quotes[self.put_to_sell.streamer_symbol].bidPrice +
            self.live_prices.quotes[self.call_to_sell.streamer_symbol].bidPrice -
            self.live_prices.quotes[self.call_to_buy.streamer_symbol].askPrice
        ) * 100


class OptionTradingApp:
    def __init__(self, root):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.start_async_loop, daemon=True).start()

        self.setup_ui(root)
        self.strategist = None  # Will be set in async initialization

    def setup_ui(self, root):
        print("Tick setup_ui")
        root.title("Option Trading Strategist")

        tk.Label(root, text="Put Sell Price:").grid(row=0, column=0, sticky="w")
        tk.Label(root, text="Put Buy Price:").grid(row=1, column=0, sticky="w")
        tk.Label(root, text="Call Sell Price:").grid(row=2, column=0, sticky="w")
        tk.Label(root, text="Call Buy Price:").grid(row=3, column=0, sticky="w")
        tk.Label(root, text="Total Winnings:").grid(row=4, column=0, sticky="w")

        self.put_sell_price_var = tk.StringVar()
        self.put_buy_price_var = tk.StringVar()
        self.call_sell_price_var = tk.StringVar()
        self.call_buy_price_var = tk.StringVar()
        self.winnings_var = tk.StringVar()

        tk.Label(root, textvariable=self.put_sell_price_var).grid(row=0, column=1, sticky="w")
        tk.Label(root, textvariable=self.put_buy_price_var).grid(row=1, column=1, sticky="w")
        tk.Label(root, textvariable=self.call_sell_price_var).grid(row=2, column=1, sticky="w")
        tk.Label(root, textvariable=self.call_buy_price_var).grid(row=3, column=1, sticky="w")
        tk.Label(root, textvariable=self.winnings_var).grid(row=4, column=1, sticky="w")

        tk.Button(root, text="Exit", command=root.quit).grid(row=5, column=0, columnspan=2, pady=10)

    def start_async_loop(self):
        print("Tick start_async_loop")
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.initialize_strategist())

    async def initialize_strategist(self):
        print("Tick initialize_strategist")
        config = TTConfig()
        session = Session(config.username, config.password, is_test=not config.use_prod)

        # Create the Strategist instance and initialize options data
        self.strategist = await Strategist.create(session, 'SPX', 5)
        await self.update_prices_periodically()

    async def update_prices_periodically(self):
        while True:
            print("Tick update_prices_periodically")
            await self.update_ui_values()
            await asyncio.sleep(1)  # Update every second

    async def update_ui_values(self):
        print("Tick update_ui_values")
        try:
            put_sell_price = self.strategist.live_prices.quotes[self.strategist.put_to_sell.streamer_symbol].bidPrice
            put_buy_price = self.strategist.live_prices.quotes[self.strategist.put_to_buy.streamer_symbol].askPrice
            call_sell_price = self.strategist.live_prices.quotes[self.strategist.call_to_sell.streamer_symbol].bidPrice
            call_buy_price = self.strategist.live_prices.quotes[self.strategist.call_to_buy.streamer_symbol].askPrice

            # Calculate winnings
            total_winnings = self.strategist.winnings()

            # Update UI elements
            self.put_sell_price_var.set(f"${put_sell_price:.2f}")
            self.put_buy_price_var.set(f"${put_buy_price:.2f}")
            self.call_sell_price_var.set(f"${call_sell_price:.2f}")
            self.call_buy_price_var.set(f"${call_buy_price:.2f}")
            self.winnings_var.set(f"${total_winnings:.2f}")

        except Exception as e:
            print(f"Error updating UI values: {e}")
            self.put_sell_price_var.set("N/A")
            self.put_buy_price_var.set("N/A")
            self.call_sell_price_var.set("N/A")
            self.call_buy_price_var.set("N/A")
            self.winnings_var.set("N/A")

root = tk.Tk()
app = OptionTradingApp(root)
root.mainloop()
