Hacking on FlashFlow
====================

Unit testing
------------

Bare ``assert`` versus ``self.assert*``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Use bare ``asserts`` in unit tests for things that you expect to be true but
are **not** about the behavior being tested. Use ``self.assert*`` for things
that **are** being tested.

For example, you have some test that verifies initial state of your state
machine is ``START``. This test creates a state machine and uses
``self.assertEqual(state_machine.state, START)`` to test this behavior.

In other unit tests where you want to make certain and/or document for the
reader that the current state is ``START`` when you are adding a widget to the
state machine's list of widgets, use a bare ``assert``.

::

   class TestWidgetStateMachine(unittest.TestCase):
      def test_add_widget(self):
         # Make explicit we are in state START. Not behavior being tested.
         assert state_machine.state == START
         w = Widget()
         state_machine.add_widget(w)
         # Verify the widget was added. Behavior being tested.
         self.assertEqual(len(state_machine.widgets), 1)

Here is another example showing proper use of ``assert`` versus
``self.assert*``. The test is not about correctly adding a widget, but removing
one.

::

   class TestWidgetStateMachine(unittest.TestCase):
      def test_remove_not_exist(self):
         # Create two widgets and add one to the list
         w_listed = Widget()
         w_unlisted = Widget()
         # Make explicit there is no widgets listed. Not being tested.
         assert not state_machine.widgets
         # Add one
         state_machine.add_widget(w_listed)
         # Make explicit there is indeed a widget listed now. Not being tested.
         assert len(state_machine.widgets) == 1
         # Here is the core of the test: try removing a non-existant widget
         state_machine.remove_widget(w_unlisted)
         # Verify the listed widget still exists. Behavior being tested.
         self.assertEqual(len(state_machine.widgets), 1)

Adding a new command
--------------------

Like ``flashflow coord`` and ``flashflow measurer``.

The working example here is a new command called ``jeff``.

Create a new file in ``flashflow/cmd/``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``flashflow/cmd/jeff.py``

Add boilerplate to new file
^^^^^^^^^^^^^^^^^^^^^^^^^^^

You need to:

- create a ``log`` object so you can log things
- define a ``gen_parser(...)`` function to call to attach your command's
  arguments to the global argument parser
- define a ``main(...)`` function

Here's a clean example that works at the time of writing.  Look at the
existing FlashFlow commands for examples that (1) definitely stay updated with
how FlashFlow functions, and (2) may be more complex than this.

::

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


Import new file in ``flashflow/flashflow.py``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

In the imports at the top of ``flashflow/flashflow.py`` find where other commands
are being imported. Import yours too. For example:

::

    # ... other imports of flashflow commands
    import flashflow.cmd.jeff

Call your new ``gen_parser(...)`` in ``flashflow/flashflow.py``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Find ``create_parser(...)`` in ``flashflow/flashflow.py`` and find where other
commands are getting their subparsers added. Do the same.

::

    # ... other lines calling commands' gen_parser() function
    flashflow.cmd.jeff.gen_parser(sub)
    return p

Add a call to your new ``main(...)`` in ``flashflow/flashflow.py``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Find ``call_real_main(...)`` in ``flashflow/flashflow.py``. In it find the
dictionary of all possible commands and the arguments to pass to them. Add your
new command.

::

    cmds = {
        # ... other commands
        'jeff': {
            'f': flashflow.cmd.jeff.main,
            'a': def_args, 'kw': def_kwargs,
        },
    }

Done
^^^^

That's it. You should be able to do things such as the following and see ``jeff``
show up.

::

    flashflow jeff
    flashflow jeff -h
    flashflow -h
