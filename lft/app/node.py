from typing import IO, Dict, Type
from lft.app.gossiper import Gossiper
from lft.app.data import DefaultDataFactory
from lft.app.vote import DefaultVoteFactory
from lft.app.logger import Logger
from lft.app.term import RotateTermFactory
from lft.event import EventSystem, EventMediator
from lft.event.mediators import DelayedEventMediator
from lft.consensus.consensus import Consensus
from lft.consensus.data import Data, Vote
from lft.consensus.events import (ReceivedDataEvent, ReceivedVoteEvent,
                                  StartRoundEvent, DoneRoundEvent, InitializeEvent)


class Node:
    def __init__(self, node_id: bytes):
        self.node_id = node_id
        self.nodes = None
        self.event_system = EventSystem()
        self.event_system.set_mediator(DelayedEventMediator)

        self.received_data = set()
        self.received_votes = set()

        self._gossipers = {}
        self._logger = Logger(self.node_id, self.event_system.simulator)
        self._consensus = Consensus(
            self.event_system,
            self.node_id,
            DefaultDataFactory(self.node_id),
            DefaultVoteFactory(self.node_id),
            RotateTermFactory(1)
        )
        self.event_system.simulator.register_handler(InitializeEvent, self._on_init_event)
        self.event_system.simulator.register_handler(DoneRoundEvent, self._on_done_round_event)

    async def _on_init_event(self, init_event: InitializeEvent):
        for gossiper in self._gossipers.values():
            await gossiper.start()
        self._nodes = init_event.voters

    async def _on_done_round_event(self, done_round: DoneRoundEvent):
        round_start_event = StartRoundEvent(
            term_num=done_round.term_num,
            round_num=done_round.round_num + 1,
            voters=self._nodes
        )
        round_start_event.deterministic = False
        mediator = self.event_system.get_mediator(DelayedEventMediator)
        mediator.execute(0.5, round_start_event)

    def __del__(self):
        self.close()

    def close(self):
        for gossiper in self._gossipers.values():
            gossiper.close()
        self._gossipers.clear()

        if self._consensus:
            self._consensus.close()
            self._consensus = None

        if self.event_system:
            self.event_system.close()
            self.event_system = None

    def start(self, blocking=True):
        self.event_system.start(blocking)

    def start_record(self, record_io: IO, mediator_ios: Dict[Type[EventMediator], IO]=None, blocking=True):
        self.event_system.start_record(record_io, mediator_ios, blocking)

    def start_replay(self, record_io: IO, mediator_ios: Dict[Type[EventMediator], IO]=None, blocking=True):
        self.event_system.start_replay(record_io, mediator_ios, blocking)

    def receive_data(self, data: Data):
        if data in self.received_data:
            pass
        else:
            self.received_data.add(data)

            event = ReceivedDataEvent(data)
            self.event_system.simulator.raise_event(event)

    def receive_vote(self, vote: Vote):
        if vote in self.received_votes:
            pass
        else:
            self.received_votes.add(vote)

            event = ReceivedVoteEvent(vote)
            self.event_system.simulator.raise_event(event)

    def register_peer(self, peer_id: bytes, peer: 'Node'):
        self._gossipers[peer_id] = Gossiper(self.event_system, self, peer)

    def unregister_peer(self, peer_id: bytes):
        self._gossipers.pop(peer_id, None)