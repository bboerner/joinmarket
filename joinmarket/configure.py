from __future__ import absolute_import, print_function

import io
import logging
import threading
import os

from ConfigParser import SafeConfigParser, NoOptionError

import bitcoin as btc
from joinmarket.jsonrpc import JsonRpc
from joinmarket.support import get_log, joinmarket_alert, core_alert, debug_silence

# config = SafeConfigParser()
# config_location = 'joinmarket.cfg'

log = get_log()


class AttributeDict(object):
    """
    A class to convert a nested Dictionary into an object with key-values
    accessibly using attribute notation (AttributeDict.attribute) instead of
    key notation (Dict["key"]). This class recursively sets Dicts to objects,
    allowing you to recurse down nested dicts (like: AttributeDict.attr.attr)
    """

    def __init__(self, **entries):
        self.add_entries(**entries)

    def add_entries(self, **entries):
        for key, value in entries.items():
            if type(value) is dict:
                self.__dict__[key] = AttributeDict(**value)
            else:
                self.__dict__[key] = value

    def __setattr__(self, name, value):
        if name == 'nickname' and value:
            logFormatter = logging.Formatter(
                ('%(asctime)s [%(threadName)-12.12s] '
                 '[%(levelname)-5.5s]  %(message)s'))
            fileHandler = logging.FileHandler('logs/{}.log'.format(value))
            fileHandler.setFormatter(logFormatter)
            log.addHandler(fileHandler)

        super(AttributeDict, self).__setattr__(name, value)

    def __getitem__(self, key):
        """
        Provides dict-style access to attributes
        """
        return getattr(self, key)

global_singleton = AttributeDict()
global_singleton.JM_VERSION = 5
global_singleton.nickname = None
global_singleton.DUST_THRESHOLD = 2730
global_singleton.bc_interface = None
global_singleton.ordername_list = ['absoffer', 'reloffer']
global_singleton.commitment_broadcast_list = ['hp2']
global_singleton.maker_timeout_sec = 60
global_singleton.debug_file_lock = threading.Lock()
global_singleton.debug_file_handle = None
global_singleton.blacklist_file_lock = threading.Lock()
global_singleton.core_alert = core_alert
global_singleton.joinmarket_alert = joinmarket_alert
global_singleton.debug_silence = debug_silence
global_singleton.config = SafeConfigParser()
global_singleton.config_location = 'joinmarket.cfg'


def jm_single():
    return global_singleton

# FIXME: Add rpc_* options here in the future!
required_options = {'BLOCKCHAIN': ['blockchain_source', 'network'],
                    'MESSAGING': ['host', 'channel', 'port'],
                    'POLICY': ['absurd_fee_per_kb', 'taker_utxo_retries',
                               'taker_utxo_age', 'taker_utxo_amtpercent']}

defaultconfig = \
    """
[BLOCKCHAIN]
blockchain_source = blockr
#options: blockr, bitcoin-rpc, regtest
# for instructions on bitcoin-rpc read
# https://github.com/chris-belcher/joinmarket/wiki/Running-JoinMarket-with-Bitcoin-Core-full-node
network = mainnet
rpc_host = localhost
rpc_port = 8332
rpc_user = bitcoin
rpc_password = password

[MESSAGING]
host = irc.cyberguerrilla.org
channel = joinmarket-pit
port = 6697
usessl = true
socks5 = false
socks5_host = localhost
socks5_port = 9050
#for tor
#host = 6dvj6v5imhny3anf.onion
#onion / i2p have their own ports on CGAN
#port = 6698
#usessl = true
#socks5 = true

[TIMEOUT]
maker_timeout_sec = 30
unconfirm_timeout_sec = 90
confirm_timeout_hours = 6

[POLICY]
# for dust sweeping, try merge_algorithm = gradual
# for more rapid dust sweeping, try merge_algorithm = greedy
# for most rapid dust sweeping, try merge_algorithm = greediest
# but don't forget to bump your miner fees!
merge_algorithm = default
# the fee estimate is based on a projection of how many satoshis
# per kB are needed to get in one of the next N blocks, N set here
# as the value of 'tx_fees'. This estimate is high if you set N=1, 
# so we choose N=3 for a more reasonable figure,
# as our default. Note that for clients not using a local blockchain
# instance, we retrieve an estimate from the API at blockcypher.com, currently.
tx_fees = 3
# For users getting transaction fee estimates over an API
# (currently blockcypher, could be others), place a sanity
# check limit on the satoshis-per-kB to be paid. This limit
# is also applied to users using Core, even though Core has its
# own sanity check limit, which is currently 1,000,000 satoshis.
absurd_fee_per_kb = 150000
# the range of confirmations passed to the `listunspent` bitcoind RPC call
# 1st value is the inclusive minimum, defaults to one confirmation
# 2nd value is the exclusive maximum, defaults to most-positive-bignum (Google Me!)
# leaving it unset or empty defers to bitcoind's default values, ie [1, 9999999]
#listunspent_args = []
# that's what you should do, unless you have a specific reason, eg:
#  !!! WARNING !!! CONFIGURING THIS WHILE TAKING LIQUIDITY FROM
#  !!! WARNING !!! THE PUBLIC ORDERBOOK LEAKS YOUR INPUT MERGES
#  spend from unconfirmed transactions:  listunspent_args = [0]
# display only unconfirmed transactions: listunspent_args = [0, 1]
# defend against small reorganizations:  listunspent_args = [3]
#   who is at risk of reorganization?:   listunspent_args = [0, 2]
# NB: using 0 for the 1st value with scripts other than wallet-tool could cause
# spends from unconfirmed inputs, which may then get malleated or double-spent!
# other counterparties are likely to reject unconfirmed inputs... don't do it.

#options: self, random-peer, not-self, random-maker
# self = broadcast transaction with your own ip
# random-peer = everyone who took part in the coinjoin has a chance of broadcasting
# not-self = never broadcast with your own ip
# random-maker = every peer on joinmarket has a chance of broadcasting, including yourself
tx_broadcast = self

#THE FOLLOWING SETTINGS ARE REQUIRED TO DEFEND AGAINST SNOOPERS.
#DON'T ALTER THEM UNLESS YOU UNDERSTAND THE IMPLICATIONS.

# number of retries allowed for a specific utxo, to prevent DOS/snooping.
# Lower settings make snooping more expensive, but also prevent honest users
# from retrying if an error occurs.
taker_utxo_retries = 3

# number of confirmations required for the commitment utxo mentioned above.
# this effectively rate-limits a snooper.
taker_utxo_age = 5

# percentage of coinjoin amount that the commitment utxo must have
# as a minimum BTC amount. Thus 20 means a 1BTC coinjoin requires the
# utxo to be at least 0.2 btc.
taker_utxo_amtpercent = 20

#Set to 1 to accept broadcast PoDLE commitments from other bots, and
#add them to your blacklist (only relevant for Makers).
#There is no way to spoof these values, so the only "risk" is that
#someone fills your blacklist file with a lot of data.
accept_commitment_broadcasts = 1
"""


def get_irc_mchannels():
    fields = [("host", str), ("port", int), ("channel", str),
              ("usessl", str), ("socks5", str), ("socks5_host", str),
              ("socks5_port", str)]
    configdata = {}
    for f, t in fields:
        vals = jm_single().config.get("MESSAGING", f).split(",")
        if t == str:
            vals = [x.strip() for x in vals]
        else:
            vals = [t(x) for x in vals]
        configdata[f] = vals
    configs = []
    for i in range(len(configdata['host'])):
        newconfig = dict([(x, configdata[x][i]) for x in configdata])
        configs.append(newconfig)
    return configs


def get_config_irc_channel(channel_name):
    channel = "#" + channel_name
    if get_network() == 'testnet':
        channel += '-test'
    return channel


def get_network():
    """Returns network name"""
    return global_singleton.config.get("BLOCKCHAIN", "network")


def get_p2sh_vbyte():
    if get_network() == 'testnet':
        return 0xc4
    else:
        return 0x05


def get_p2pk_vbyte():
    if get_network() == 'testnet':
        return 0x6f
    else:
        return 0x00


def validate_address(addr):
    try:
        ver = btc.get_version_byte(addr)
    except AssertionError:
        return False, 'Checksum wrong. Typo in address?'
    if ver != get_p2pk_vbyte() and ver != get_p2sh_vbyte():
        return False, 'Wrong address version. Testnet/mainnet confused?'
    if len(btc.b58check_to_bin(addr)) != 20:
        return False, "Address has correct checksum but wrong length."
    return True, 'address validated'


def check_utxo_blacklist(commitment):
    """Compare a given commitment (H(P2) for PoDLE)
    with the persisted blacklist log file;
    if it has been used before, return False (disallowed),
    else return True.
    Persist the usage of this commitment to the blacklist file.
    """
    #TODO format error checking?
    fname = "blacklist"
    if jm_single().config.get("BLOCKCHAIN", "blockchain_source") == 'regtest':
        fname += "_" + jm_single().nickname
    with jm_single().blacklist_file_lock:
        if os.path.isfile(fname):
                with open(fname, "rb") as f:
                    blacklisted_commitments = [x.strip() for x in f.readlines()]
        else:
            blacklisted_commitments = []
        if commitment in blacklisted_commitments:
            return False
        else:
            blacklisted_commitments += [commitment]
        with open(fname, "wb") as f:
            f.write('\n'.join(blacklisted_commitments))
            f.flush()
    return True


def load_program_config():
    global_singleton.config.readfp(io.BytesIO(defaultconfig))
    loadedFiles = global_singleton.config.read([global_singleton.config_location
                                               ])
    # Create default config file if not found
    if len(loadedFiles) != 1:
        with open(global_singleton.config_location, "w") as configfile:
            configfile.write(defaultconfig)

    # check for sections
    for s in required_options:
        if s not in global_singleton.config.sections():
            raise Exception(
                "Config file does not contain the required section: " + s)
    # then check for specific options
    for k, v in required_options.iteritems():
        for o in v:
            if o not in global_singleton.config.options(k):
                raise Exception(
                        "Config file does not contain the required option: " + o +\
                        " in section: " + k)

    try:
        global_singleton.maker_timeout_sec = global_singleton.config.getint(
            'TIMEOUT', 'maker_timeout_sec')
    except NoOptionError:
        log.debug('TIMEOUT/maker_timeout_sec not found in .cfg file, '
                  'using default value')

    # configure the interface to the blockchain on startup
    global_singleton.bc_interface = get_blockchain_interface_instance(
        global_singleton.config)

    #print warning if not using libsecp256k1
    if not btc.secp_present:
        log.debug(
            "WARNING: You are not using the binding to libsecp256k1. The "
            "crypto code in use has poorer performance and security "
            "properties. Consider installing the binding with `pip install "
            "secp256k1`.")


def get_blockchain_interface_instance(_config):
    # todo: refactor joinmarket module to get rid of loops
    # importing here is necessary to avoid import loops
    from joinmarket.blockchaininterface import BitcoinCoreInterface, \
        RegtestBitcoinCoreInterface, BlockrInterface
    from joinmarket.blockchaininterface import CliJsonRpc

    source = _config.get("BLOCKCHAIN", "blockchain_source")
    network = get_network()
    testnet = network == 'testnet'
    if source == 'bitcoin-rpc':
        rpc_host = _config.get("BLOCKCHAIN", "rpc_host")
        rpc_port = _config.get("BLOCKCHAIN", "rpc_port")
        rpc_user = _config.get("BLOCKCHAIN", "rpc_user")
        rpc_password = _config.get("BLOCKCHAIN", "rpc_password")
        rpc = JsonRpc(rpc_host, rpc_port, rpc_user, rpc_password)
        bc_interface = BitcoinCoreInterface(rpc, network)
    elif source == 'json-rpc':
        bitcoin_cli_cmd = _config.get("BLOCKCHAIN",
                                      "bitcoin_cli_cmd").split(' ')
        rpc = CliJsonRpc(bitcoin_cli_cmd, testnet)
        bc_interface = BitcoinCoreInterface(rpc, network)
    elif source == 'regtest':
        rpc_host = _config.get("BLOCKCHAIN", "rpc_host")
        rpc_port = _config.get("BLOCKCHAIN", "rpc_port")
        rpc_user = _config.get("BLOCKCHAIN", "rpc_user")
        rpc_password = _config.get("BLOCKCHAIN", "rpc_password")
        rpc = JsonRpc(rpc_host, rpc_port, rpc_user, rpc_password)
        bc_interface = RegtestBitcoinCoreInterface(rpc)
    elif source == 'blockr':
        bc_interface = BlockrInterface(testnet)
    else:
        raise ValueError("Invalid blockchain source")
    return bc_interface
