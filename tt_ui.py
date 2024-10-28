import tkinter as tk
import asyncio
import threading
from lib import TTConfig, TTApi, TTOption, TTOptionSide, TTOrder, TTTimeInForce, TTPriceEffect, TTOrderType, TTLegAction, TTInstrumentType

class TastyTradeApp:
    def __init__(self, root):
        self.api = TTApi()
        self.flash_color = True

        if not self.login_and_validate():
            root.destroy()
            return

        self.setup_ui(root)

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.start_async_loop, daemon=True).start()

        self.start_flashing()

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

    def setup_ui(self, root):
        root.title("TastyTrade Option Spread")

        tk.Label(root, text="Profit/Loss:").grid(row=0, column=0, sticky="w")
        tk.Label(root, text="Margin Required:").grid(row=1, column=0, sticky="w")
        tk.Label(root, text="Update Status:").grid(row=2, column=0, sticky="w")

        self.profit_var = tk.StringVar()
        self.margin_var = tk.StringVar()

        self.update_status_label = tk.Label(root, text="Updating...", fg="green")
        self.update_status_label.grid(row=2, column=1, sticky="w")

        tk.Label(root, textvariable=self.profit_var).grid(row=0, column=1, sticky="w")
        tk.Label(root, textvariable=self.margin_var).grid(row=1, column=1, sticky="w")

        exit_button = tk.Button(root, text="Exit", command=root.quit)
        exit_button.grid(row=3, column=0, columnspan=2, pady=10)

    def start_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.update_data_periodically())

    async def update_data_periodically(self):
        while True:
            await self.update_trade_details()
            await asyncio.sleep(1)

    async def update_trade_details(self):
        call_to_sell = TTOption('SPXW', '241030', TTOptionSide.CALL, 5850)
        call_to_buy = TTOption('SPXW', '241030', TTOptionSide.CALL, 5950)

        order = TTOrder(TTTimeInForce.GTC, 0.05, TTPriceEffect.DEBIT, TTOrderType.LIMIT)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, call_to_sell.symbol, 1, TTLegAction.STO)
        order.add_leg(TTInstrumentType.EQUITY_OPTION, call_to_buy.symbol, 1, TTLegAction.BTO)

        try:
            order_response = self.api.simple_order(order)

            legs = order_response.get("legs", [])
            sell_leg_price = 0.0
            buy_leg_price = 0.0

            for leg in legs:
                if leg["action"] == "Sell to Open":
                    sell_leg_price = float(leg["price"])
                elif leg["action"] == "Buy to Open":
                    buy_leg_price = float(leg["price"])

            net_premium = sell_leg_price - buy_leg_price

            self.profit_var.set(f"${net_premium:.2f}")
            margin_required = float(order_response["buying-power-effect"]["isolated-order-margin-requirement"])
            self.margin_var.set(f"${margin_required:.2f}")

        except Exception as e:
            print(f"Error updating trade details: {e}")
            self.profit_var.set("N/A")
            self.margin_var.set("N/A")


    def start_flashing(self):
        """Starts the flashing effect to confirm updates."""
        new_color = "green" if self.flash_color else "blue"
        self.update_status_label.config(fg=new_color)
        self.flash_color = not self.flash_color
        self.update_status_label.after(500, self.start_flashing)

root = tk.Tk()
app = TastyTradeApp(root)
root.mainloop()
