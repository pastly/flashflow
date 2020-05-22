# Adding a new command

Like `flashflow coord` and `flashflow measurer`.

The working example here is a new command called `jeff`.

## Create a new file in `flashflow/cmd/`

`flashflow/cmd/jeff.py`

## Add boilerplate to new file

You need to:

- create a `log` object so you can log things
- define a `gen_parser(...)` function to call to attach your command's
  arguments to the global argument parser
- define a `main(...)` function

Here's a clean example that works at the time of writring.  Look at the
existing FlashFlow commands for examples that (1) definitely stay updated with
how FlashFlow functions, and (2) may be more complex than this.

    from argparse import ArgumentParser
    import logging
    
    
    log = logging.getLogger(__name__)
    
    
    def gen_parser(sub) -> ArgumentParser:
        ''' Add the cmd line options for this FlashFlow command '''
        d = 'The example FlashFlow command known as jeff'
        p = sub.add_parser('jeff', description=d)
        return p
    
    
    # This function needs **some sort** of type annotation so that mypy will check
    # the things it does. Adding the return value (e.g. '-> None') is enough
    def main(args, conf) -> None:
        log.error('Hi I'm jeff, and I am boilerplate. Make me do something useful')


## Import new file in `flashflow/flashflow.py`

In the imports at the top of `flashflow/flashflow.py` find where other commands
are being imported. Import yours too. For example:

    # ... other imports of flashflow commands
    import flashflow.cmd.jeff

## Call your new `gen_parser(...)` in `flashflow/flashflow.py`

Find `create_parser(...)` in `flashflow/flashflow.py` and find where other
commands are getting their subparsers added. Do the same.

    # ... other lines calling commands' `gen_parser()` function
    flashflow.cmd.jeff.gen_parser(sub)
    return p

## Add a call to your new `main(...)` in `flashflow/flashflow.py`.

Find `call_real_main(...)` in `flashflow/flashflow.py`. In it find the
dictionary of all possible commands and the arguments to pass to them. Add your
new command.

    cmds = {
        # ... other commands
        'jeff': {
            'f': flashflow.cmd.jeff.main,
            'a': def_args, 'kw': def_kwargs,
        },
    }

## Done

That's it. You should be able to do things such as the following and see `jeff`
show up.

    flashflow jeff
    flashflow jeff -h
    flashflow -h

# Adding a new message (coord <--> measurer)

These messages are defined in `flashflow/msg.py`.

The working example will be a new message `Foo`.

## Define a new `MsgType` enum variant

Find `class MsgType` and add a new variant. Use a random integer for its value;
just smash yo' keyboard.

    class MsgType(enum.Enum):
        # ... docs and other variants
        FOO = 19874

## Check for the new variant in `FFMsg.deserialize(...)`

This function is called to deserialize incoming messages. It checks the type of
the incoming message and passes control off to the appropriate class for
further deserialization. Check for your enum variant in the if/elif chain.

    elif msg_type == MsgType.FOO:
        return Foo.from_dict(j)

## Define your new `class Foo`

The following is required.

1. Set a `msg_type` class variable set to `MsgType.FOO`.
2. Define a `serialize(...)` method that takes self and returns bytes
3. Define a `from_dict(...)` that takes a dictionary and returns a valid `Foo` object.

The serialize function **MUST** return JSON as a byte string, and the JSON **MUST**
include the `msg_type` as well as all other fields necessary to construct a new
valid instance of `Foo`.

The `from_dict` function **MUST** be able to take a dictionary constructed from
such a JSON byte string and create a new valid instance of `Foo`.

    class Foo(FFMsg):
        # (1) setting msg_type
        msg_type = MsgType.FOO
    
        def __init__(self, i: int):
            self.i = i
    
        # (2) serialize that returns a JSON byte string with all necessary
        # fields and msg_type as an int
        def serialize(self) -> bytes:
            return json.dumps({
                'msg_type': self.msg_type.value,
                'i': self.i,
            }).encode('utf-8')
    
        # (3) from_dict that can take a dictionary constructed from a JSON byte
        # string that serialize() output
        @staticmethod
        def from_dict(d: dict) -> 'Foo':
            return Foo(d['i'])
