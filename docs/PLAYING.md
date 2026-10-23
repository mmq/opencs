# Playing on an OpenCS server

How to connect the original DOS client to an OpenCS server and what to do once you are in. The server's
side is in `operations.md` and `DEPLOYMENT.md`.

## What you need

- **Your own copy of the game.** OpenCS does not include it and this project never distributes it: no
  program, art, map or manual. Install it the way its installer says.
- **DOSBox-X or DOSBox Staging**, or any DOS setup with an emulated modem that can dial out over the
  internet. Both emulators' modems take a phonebook file that turns the number the game dials into a
  server address. (The original DOSBox has no phonebook.)
- **The address of a server**: a host name or IP address and a TCP port (the default is 2399). If you run
  the server yourself, see `DEPLOYMENT.md`.

## DOSBox-X or DOSBox Staging: the serial port as a modem

The game thinks it is dialing a modem on a serial port. DOSBox-X and DOSBox Staging both emulate that
modem and, when the game "dials" a number, open a TCP connection to whatever address the phonebook maps
the number to.

The settings that matter are the same in both, in the emulator's config file (`dosbox-x.conf`,
`dosbox-staging.conf`, or one you pass with `-conf`):

```ini
[dos]
umb=true
ems=true
xms=true

[cpu]
core=dynamic

[serial]
phonebookfile = /full/path/to/phonebook.txt
serial1=modem
```

The game needs expanded memory (`ems`). It runs smoothly with the dynamic core and about 25000 cycles:
`cycles=25000` in DOSBox-X, `cpu_cycles = 25000` in DOSBox Staging.

The phonebook is a text file; give its full path. Each line is a number, a space and the `host:port` it
maps to. Map the number the game dials (next section) to your server, or to `localhost` for a server on
your own machine:

```
1234 play.example.org:2399
1111 localhost:2399
```

Then mount the folder holding the game and start it from `[autoexec]` as you normally would:

```ini
[autoexec]
mount c /path/to/your/game
c:
CS.COM
```

(`CS.COM` is the game's start program; the folder name and the way the game was installed are yours.)

### The game's settings: dial number, baud rate, account name and password

Before you log on, press the game's SETTINGS button and set:

- the **phone number** to the one in your phonebook line (`1234` above);
- the **baud rate** to **9600**;
- your **account name** and **password**, as described next, or leave the stock guest name `JOINGENIE`.

A name or password **must not contain a space**, and the password is **case-sensitive**: type it the same
way every time. The game saves these settings in `CS.CFG`, in its folder.

## Logging in

Press LOGON. The game runs its own logon script against the server; you only choose the identity, in
SETTINGS as above.

| account name | what you get |
|---|---|
| `JOINGENIE` (the stock install) | **A guest.** No password is sent. You are given a callsign: the name itself (`JOINGENIE`), with a number after it if that callsign is already online (`JOINGENIE2`, `JOINGENIE3`...). Two unmodified clients can therefore play together. Nothing a guest earns is kept: every game starts as a new player's would, and guests cannot `/RESUME`. A server may name other guest names, or turn guests off. |
| any other name, with a password | **A real account.** The first login with a name registers its password (there is no sign-up step); later logins must give the same one. The callsign is the name, upper-cased and cut to 12 characters, with its last characters replaced by a number if that callsign is already online (`ALEXANDERTHE1` and `ALEXANDERTHE2` become `ALEXANDERTHE` and `ALEXANDERTH2`, whichever logs in first gets the plain one). Progress is saved to the account, and `/RESUME` works. Only one login per account at a time. |

Names may be up to 32 characters and passwords up to 64. A name other than a guest name with no password,
a wrong password, a name that is already online, or too many attempts in a short time is refused with a
line saying so. Choose a password you use nowhere else: the server stores only a salted hash, but the
link is plain text.

After the script finishes the game shows the online-service menu. Press GO (or F1); the Chamber opens.

## The Chamber

The Chamber is the lobby. You are in it when the button bar shows PLAY, MORE and QUIT. Type a line and
press RETURN. Commands begin with `/`, in either case:

| you type | the server does |
|---|---|
| `/HELP` | lists the commands |
| `/CITY` | one line per open city: its letter, name and pilots/capacity |
| `/CITY <letter>` | that city's four teams, how many pilots each has, and their callsigns |
| `/PLAY` | joins the busiest city that has room; when all are empty, city B |
| `/PLAY <letter> [team]` | joins that city. `team` is `RED`, `BLUE`, `GREEN`, `BLACK`, or `R`, `B`, `G`, or `0` to `3` (`B` is BLUE; use `BLACK` or `3` for black). If the team you name is full, or the city chooses teams, you are put on the smallest open team and told so; with no team named you get no such line |
| `/WHO` or `/STATUS` | every pilot online and where (`CHAMBER`, or city letter and team) |
| `/WHO <callsign>` | that pilot's location and saved record |
| `/SCORE` | your own saved record: last game's score, career total, bay points, bays, battle marker |
| `/RESUME` | return to a game a dropped line interrupted (below) |
| `/COLORS` | shows the colors the server uses for text (a test aid) |
| `/QUIT` | leave the Chamber for the online-service prompt; type `BYE` there to hang up |
| anything else | sent as chat to the other pilots in the Chamber (not to anyone in a game), as `CALLSIGN: text` |

The button bar types for you: PLAY sends `/PLAY`; MORE shows a second row (STATUS, WHO, CITY, SCORE and
the city variants that type `/CITY ` or `/PLAY ` onto your line, and the callsign button that types
`/WHO `, for you to finish). Typing a single city letter shortly after `/CITY` gets a reminder of how to ask for information
or to play. A command the server does not know is answered with an "unknown command" line.

(`/GO` is not a command you type: the game sends it itself while launching, to say it found the city's
map file.)

### The three cities

Cities are set by the operator (`config/cities/`); the shipped ones are:

| letter | name | map | multiplier ceiling | towers | modules in stock | hazards |
|---|---|---|---|---|---|---|
| A | CYBERCITY ONE | 1 | 8 | Normal output | up to level 2 | none: a place to learn |
| B | CYBERCITY TWO | 2 | 20 | Normal output | up to level 4 | 2 Sentinels; rain that comes and goes |
| C | CYBERCITY THREE | 3 | 40 | Low output | up to level 5 | 4 Sentinels, a free Muncher every 10 minutes; dusk and storms |

Each city's territory is worth different amounts per block (A: a raised center, B: rings rising to the
center, C: a high-value core), so where you fight matters. Each city holds 16 pilots, 4 per team.
**Teams** are RED, BLUE, GREEN and BLACK; the team decides your home corner. B is the default city.

## In a game

You enter with `/PLAY`; the game shows its loading screen and then drops you into the city. Play is
the game's own. What the server does for you:

- **Orders arrive by guppy.** When you buy a tower or a module, your team's guppy flies the delivery
  from your corner; it lands after its flight, not at once. Towers then belong to your team and earn
  territory.
- **Towers, modules and effects.** Modules are limited by the city's stock level; the server carries out
  the modules' effects. A dropped module can be picked up.
- **Bounty and payouts.** The server keeps every pod's bounty and pays it to those who hit and kill
  you. A death resets your power, multiplier and modules; your battle marker drops by one.
- **Bays persist.** The bays you have upgraded and your battle marker are saved with your account and
  restored next time you enter a city. A guest's are not kept.
- **Latency meter.** The server answers the game's ping at once; the meter shows your round trip.

To leave, press **Shift-Q**: the game saves your progress, closes and returns you to the Chamber on the
same connection. After a few seconds the Chamber buttons come back and you can `/PLAY` again without
logging in. An administrator may also send you back, with a message, or announce a restart.

### If your line drops

A real account that loses its connection (crash, closed window, lost link) has the game **held**:

1. Reconnect and log in within **10 minutes** of the drop.
2. In the Chamber the server tells you the game was interrupted and how long you have. You have **3
   minutes after re-entering the Chamber** to type `/RESUME`.
3. `/RESUME` launches you back into the same city and team. Restored: bays and battle marker, score,
   multiplier, bay points and installed modules (as of your last automatic status save, about a
   minute). Not restored: power and armor (you start at full), module charges, bounty and position
   (you drop at your team's corner).

`/RESUME` is **refused for guests**, when nothing is held, and **after a drop in the middle of a fight**:
if your pod was under attack in the 20 seconds before the line dropped, the drop counts as a death, with
payouts to the pilots who hit you, and cannot be resumed. Typing `/PLAY` gives the held game up. A server
restart loses every held game.

## Troubleshooting

| symptom | cause and fix |
|---|---|
| The Chamber works but, in a game, nothing you do has any effect, or the screen shows garbage | The link is not 8-bit clean. The game's packets need 8 data bits, no parity and 1 stop bit (I §3): if your emulator's config sets the serial port's format, set it to that, and make sure nothing between you and the server (a proxy, a telnet-mode bridge) alters bytes |
| After `/PLAY` a line says there is no map and you stay in the Chamber (the game answered `/GO 1`) | The game could not find the map file the city uses. Only maps 1 to 3 ship with the game; a city on another map needs the file from the operator, placed in the game's folder |
| A `/GO 2` or `/GO 3` line | The map does not match the game, or the PC has too little memory free for the game: free conventional or expanded memory (the game needs about 580 KB and expanded memory) and try again |
| The pod is frozen right after loading, or other pilots do not move | The server may be busy or the link slow; wait a few seconds. If it persists, press Shift-Q, or reconnect. Check the link as in the first row |
| The latency meter stays blank | The ping is not being answered, which the server does in every game, so the in-game link is broken: see the first row |
| Nothing after dialing, or "NO CARRIER" | The number you dialed is not in the phonebook, the `host:port` is wrong or unreachable, or a firewall blocks the port. Test from the same machine with `nc host port` |
| The server says `THAT ACCOUNT IS ALREADY LOGGED IN` | The account has a live connection (perhaps your old dropped one, until the server notices it is gone, which takes about 15 seconds in a game and up to 2 minutes in the Chamber); wait and retry |
| `TOO MANY LOGIN ATTEMPTS` or `TOO MANY NEW ACCOUNTS` | The server limits how fast logins and sign-ups can come from one address; wait a minute |
| `/RESUME` says nothing is held | You are a guest, the 10 or 3 minutes passed, you used `/PLAY` in between, the drop counted as a death, or the server restarted |
| The server is full: the connection closes at once | A limit of 8 connections per address and 64 in all applies; tell the operator if you share an address with others |

The text of the game and its manual are not repeated here; the manual that came with your copy is the
reference for flying, weapons and modules.
