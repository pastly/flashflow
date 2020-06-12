- [ ] coord sends config stuff to measurers. Such as timeouts
- [x] measr's tor client send echo traffic.
- [x] relay echo traffic.
- [x] relay report BG traffic.
- [x] more than one measurement circuit (and connection) with relay.
- [x] relay handle echo traffic separately.
- [.] relay limit about of BG traffic.
    - [ ] verify bg traffic isn't unfairly crowded out
- [ ] measr and its tor client handle multiple measurements at once.
- [ ] coord and its tor client handle multiple measurements at once.
- [x] actually use 30s from params everywhere
- [x] actually cap relay's reported bg traffic at the coord
- [ ] generate v3bw files
- [x] configurable bg traffic percent at coord
- [ ] revisit/add docs [noted 2020-06-12]
- [ ] revisit/add tests [noted 2020-06-12]
- [ ] think about who does what at the end of a measurement. who times out and
  tells others about failure? too many parties can cause the end in too many
ways right now
- [ ] think about: do we need the transitions package for a state machine? is
  it actually helping?
- [ ] coord scheduling measurements throughout the day
- [ ] edit proposal
