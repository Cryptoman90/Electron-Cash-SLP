import copy
import datetime
from functools import partial
import json
import threading

from PyQt5.QtCore import *
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *

from electroncash.address import Address, PublicKey
from electroncash.bitcoin import base_encode, TYPE_ADDRESS
from electroncash.i18n import _
from electroncash.plugins import run_hook

from electroncash.util import bfh
from .util import *

from electroncash.util import format_satoshis
from electroncash.transaction import Transaction
from electroncash.slp import SlpMessage, SlpNoMintingBatonFound, SlpUnsupportedSlpTokenType, SlpInvalidOutputMessage, SlpTokenTransactionFactory

dialogs = []  # Otherwise python randomly garbage collects the dialogs...

class SlpAddTokenMintDialog(QDialog, MessageBoxMixin):

    def __init__(self, main_window, token_id_hex):
        # We want to be a top-level window
        QDialog.__init__(self, parent=main_window)

        self.main_window = main_window
        self.wallet = main_window.wallet
        self.network = main_window.network
        self.app = main_window.app

        self.setWindowTitle(_("Mint Additional Tokens"))

        vbox = QVBoxLayout()
        self.setLayout(vbox)

        vbox.addWidget(QLabel(_('Token ID:')))
        self.token_id_e = ButtonsLineEdit()
        self.token_id_e.addCopyButton(self.app)
        self.token_id_e.setFixedWidth(550)
        self.token_id_e.setText(token_id_hex)
        self.token_id_e.setDisabled(True)
        vbox.addWidget(self.token_id_e)

        vbox.addWidget(QLabel(_('Additional Token Quantity:')))
        self.token_qty_e = ButtonsLineEdit()
        self.token_qty_e.setFixedWidth(75)
        vbox.addWidget(self.token_qty_e)

        vbox.addWidget(QLabel(_('Token Receiver Address:')))
        self.token_pay_to_e = ButtonsLineEdit()
        if token_id_hex is not None:
            self.token_pay_to_e.addCopyButton(self.app)
        self.token_pay_to_e.setFixedWidth(400)
        vbox.addWidget(self.token_pay_to_e)

        vbox.addWidget(QLabel(_('Mint Baton Address:')))
        self.token_baton_to_e = ButtonsLineEdit()
        if token_id_hex is not None:
            self.token_baton_to_e.addCopyButton(self.app)
        self.token_baton_to_e.setFixedWidth(400)
        vbox.addWidget(self.token_baton_to_e)

        hbox = QHBoxLayout()
        vbox.addLayout(hbox)

        self.cancel_button = b = QPushButton(_("Cancel"))
        self.cancel_button.setAutoDefault(False)
        self.cancel_button.setDefault(False)
        b.clicked.connect(self.close)
        b.setDefault(True)
        hbox.addWidget(self.cancel_button)

        hbox.addStretch(1)

        self.mint_button = b = QPushButton(_("MINT")) #if self.provided_token_name is None else _("Change"))
        b.clicked.connect(self.mint_token)
        self.mint_button.setAutoDefault(True)
        self.mint_button.setDefault(True)
        hbox.addWidget(self.mint_button)

        dialogs.append(self)
        self.show()

        self.token_qty_e.setFocus()

    def parse_address(self, address):
        if "simpleledger" not in address:
            address="simpleledger:"+address
        return Address.from_string(address)        

    def mint_token(self):
        mint_baton_vout = 2 if self.token_baton_to_e.text() != '' else None
        try:
            init_mint_qty = int(self.token_qty_e.text())
        except Exception as e:
            self.main_window.show_message(_("Must have initial token quantity entered."))
            return

        outputs = []
        msgFactory = SlpTokenTransactionFactory(1, self.token_id_e.text())
        slp_op_return_msg = msgFactory.buildMintOpReturnOutput_V1(mint_baton_vout, init_mint_qty)
        outputs.append(slp_op_return_msg)

        addr = self.parse_address(self.token_pay_to_e.text())
        outputs.append((TYPE_ADDRESS, addr, 546))

        if self.token_baton_to_e.text() != '':
            addr = self.parse_address(self.token_baton_to_e.text())
            outputs.append((TYPE_ADDRESS, addr, 546))

        coins = self.main_window.get_coins()
        fee = None
        try:
            baton_input = self.main_window.wallet.get_slp_token_baton(self.token_id_e.text())
        except SlpNoMintingBatonFound as e:
            self.main_window.show_message(_("No baton exists for this token."))

        try:
            tx = self.main_window.wallet.make_unsigned_transaction(coins, outputs, self.main_window.config, fee, None)
        except NotEnoughFunds:
            self.main_window.show_message(_("Insufficient funds"))
            return
        except ExcessiveFee:
            self.main_window.show_message(_("Your fee is too high.  Max is 50 sat/byte."))
            return
        except BaseException as e:
            traceback.print_exc(file=sys.stdout)
            self.main_window.show_message(str(e))
            return

        # Find & Add baton to tx inputs
        try:
            baton_utxo = self.main_window.wallet.get_slp_token_baton(self.token_id_e.text())
        except SlpNoMintingBatonFound:
            self.main_window.show_message(_("There is no minting baton found for this token."))
            return

        tx.add_inputs([baton_utxo])
        for txin in tx._inputs:
            self.main_window.wallet.add_input_info(txin)

        # TODO: adjust change amount (based on amount added from baton)

        msg = []

        if self.main_window.wallet.has_password():
            msg.append("")
            msg.append(_("Enter your password to proceed"))
            password = self.main_window.password_dialog('\n'.join(msg))
            if not password:
                return
        else:
            password = None

        tx_desc = None

        def sign_done(success):
            if success:
                if not tx.is_complete():
                    self.main_window.show_transaction(tx)
                    self.main_window.do_clear()
                else:
                    self.main_window.broadcast_transaction(tx, tx_desc)

        self.main_window.sign_tx_with_password(tx, sign_done, password)

        self.mint_button.setDisabled(True)
        self.close()

    def closeEvent(self, event):
        event.accept()
        dialogs.remove(self)

    def update(self):
        return