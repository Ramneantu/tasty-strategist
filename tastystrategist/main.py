import asyncio
import tkinter as tk

from tastytrade import Session, Account

from tastystrategist import Strategist
from tastystrategist import TTConfig
from tastystrategist.position import PositionState

async def main():
    config = TTConfig(filename='tt.config')
    config_sandbox = TTConfig(filename='tt.sandbox.config')
    session = Session(config.username, config.password, is_test=not config.use_prod)
    session_sandbox = Session(config_sandbox.username, config_sandbox.password, is_test=not config_sandbox.use_prod)
    account_sandbox = Account.get_accounts(session_sandbox)[0]
    print(f'Account number: {account_sandbox.account_number}')

    strategist = await Strategist.create(session, session_sandbox, account_sandbox, 'SPX', 'SPXW')

    root = tk.Tk()
    root.title("Strategist Winnings")

    winnings_label = tk.Label(root, text="Calculating...", font=("Helvetica", 30))
    winnings_label.pack(pady=10)
    
    put_to_buy_label = tk.Label(root, text="Put to Buy: -", font=("Helvetica", 20))
    put_to_buy_label.pack()

    put_to_sell_label = tk.Label(root, text="Put to Sell: -", font=("Helvetica", 20))
    put_to_sell_label.pack()

    call_to_sell_label = tk.Label(root, text="Call to Sell: -", font=("Helvetica", 20))
    call_to_sell_label.pack()

    call_to_buy_label = tk.Label(root, text="Call to Buy: -", font=("Helvetica", 20))
    call_to_buy_label.pack()

    margin_label = tk.Label(root, text="Margin: -", font=("Helvetica", 30))
    margin_label.pack(pady=10)
    
    positions_label = tk.Label(root, text="Open Positions: 0", font=("Helvetica", 20))
    positions_label.pack(pady=10)
    
    order_button = tk.Button(root, text="Open Order", bg="green", fg="black", font=("Helvetica", 20))
    order_button.pack(pady=10)

    async def toggle_order(strategist: Strategist):
        current_text = order_button.cget("text")
        if current_text == "Open Order":
            await strategist.position_manager.open_position(strategist.session_sandbox, strategist.sandbox_account, dry_run=False)
            order_button.config(text="Close Order", bg="red")
        else:
            await strategist.position_manager.close_position(strategist.session_sandbox, strategist.sandbox_account, dry_run=False)
            order_button.config(text="Open Order", bg="green")

    # Change the button command to use asyncio.create_task
    order_button.config(command=lambda: asyncio.create_task(toggle_order(strategist)))

    def update_labels():
        if strategist.position_manager.state <= PositionState.NO_POSITION:
            winnings_label.config(text="Wait for strategy to initialize...")
        elif PositionState.PENDING <= strategist.position_manager.state and strategist.position_manager.state <= PositionState.OPENING_REQUESTED:
            winnings_label.config(text=f"Estimated Opening Earnings: ${strategist.estimated_buying_power_effect_open()}")
            put_to_buy_label.config(
                text=f"Open Insurance Put ({strategist.position_manager.position.insurance_put.symbol}): "
                        f"${strategist.get_insurance_put_price()}"
            )
            put_to_sell_label.config(
                text=f"Open Main Put ({strategist.position_manager.position.main_put.symbol}): "
                        f"${strategist.get_main_put_price()}"
            )
            call_to_sell_label.config(
                text=f"Open Main Call ({strategist.position_manager.position.main_call.symbol}): "
                        f"${strategist.get_main_call_price()}"
            )
            call_to_buy_label.config(
                text=f"Open Insurance Call ({strategist.position_manager.position.insurance_call.symbol}): "
                        f"${strategist.get_insurance_call_price()}"
            )
            positions_label.config(
                text=f"Open Positions: {strategist.position_manager.account_updates.num_open_positions()}"
            )
            margin_label.config(
                text=f"Margin required: {(f'${strategist.position_manager.margin_requirement_no_wait():.2f}') if strategist.position_manager.margin_requirement_no_wait() is not None else 'N/A'}"
            )
        elif PositionState.OPEN <= strategist.position_manager.state and strategist.position_manager.state <= PositionState.CLOSING_REQUESTED:
            winnings_label.config(text=f"Estimated Earnings: ${strategist.estimated_buying_power_effect() if strategist.estimated_buying_power_effect() is not None else 'N/A'}")
            put_to_buy_label.config(
                text=f"Close Insurance Put ({strategist.position_manager.position.insurance_put.symbol}): "
                        f"${strategist.get_insurance_put_price(buy=False)}"
            )
            put_to_sell_label.config(
                text=f"Close Main Put ({strategist.position_manager.position.main_put.symbol}): "
                        f"${strategist.get_main_put_price(buy=True)}"
            )
            call_to_sell_label.config(
                text=f"Close Main Call ({strategist.position_manager.position.main_call.symbol}): "
                        f"${strategist.get_main_call_price(buy=True)}"
            )
            call_to_buy_label.config(
                text=f"Close Insurance Call ({strategist.position_manager.position.insurance_call.symbol}): "
                        f"${strategist.get_insurance_call_price(buy=False)}"
            )
            margin_label.config(
                text=f"No Margin required anymore"
            )
            positions_label.config(
                text=f"Open Positions: {strategist.position_manager.account_updates.num_open_positions()}"
            )
        elif strategist.position_manager.state == PositionState.CLOSED:
            # None check should not be necessary
            winnings_label.config(text=f"Actual Earnings: ${strategist.buying_power_effect() if strategist.buying_power_effect() is not None else 'N/A'}")
            put_to_buy_label.config(
                text=f"Insurance Put ({strategist.position_manager.position.insurance_put.symbol}): closed"
            )
            put_to_sell_label.config(
                text=f"Main Put ({strategist.position_manager.position.main_put.symbol}): closed"
            )
            call_to_sell_label.config(
                text=f"Main Call ({strategist.position_manager.position.main_call.symbol}): closed"
            )
            call_to_buy_label.config(
                text=f"Insurance Call ({strategist.position_manager.position.insurance_call.symbol}): closed"
            )
            positions_label.config(
                text=f"Open Positions: {strategist.position_manager.account_updates.num_open_positions()}"
            )

    async def tkinter_update():
        while True:
            update_labels()
            root.update_idletasks()
            root.update()
            await asyncio.sleep(0.1)

    # Main UI "thread"
    await tkinter_update()

    await strategist.live_prices.close_channel()
    session.destroy()

if __name__ == '__main__':
    asyncio.run(main())