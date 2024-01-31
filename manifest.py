# Modules to freeze into a custom micropython build.
# We can run on a standard micropython build, but these
# modules will need to be added to :/lib.
#
# You'll also need some secrets files, either frozen in
# or on the board's FS (if supported). Samples for these
# are at https://github.com/swork/public-secrets.git
#
# There are some builtins that can't be frozen, maybe
# they're completely in C in upy already? Here's the list:
#
# require('gc')
# require('machine')
# require('network')
# require('sys')
#
# And this one is just a pothole, lurking to break your wheel.
# It references re.VERBOSE and other flags, which aren't
# available or supported in the internal re module
# (micropython/lib/re1.5). micropython's internal modjson.c
# does the simple stuff so this isn't needed; I'm guessing
# it should have been removed when modjson.c was renamed
# from modujson.c.
#
# require('json')
#

require('aiohttp')
require('datetime')
require('os')
require('time')
require('unittest')
