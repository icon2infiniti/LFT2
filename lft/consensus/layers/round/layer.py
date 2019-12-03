import logging
from lft.consensus.layers.round import RoundMessages
from lft.consensus.messages.data import Data, DataFactory, DataPool, DataVerifier
from lft.consensus.messages.vote import Vote, VoteFactory, VotePool
from lft.consensus.events import (RoundEndEvent, BroadcastDataEvent, BroadcastVoteEvent,
                                  ReceiveDataEvent, ReceiveVoteEvent)
from lft.consensus.epoch import Epoch
from lft.consensus.exceptions import InvalidProposer
from lft.event import EventSystem


class RoundLayer:
    def __init__(self,
                 node_id: bytes,
                 epoch: Epoch,
                 round_num: int,
                 event_system: EventSystem,
                 data_factory: DataFactory,
                 vote_factory: VoteFactory,
                 data_pool: DataPool,
                 vote_pool: VotePool):
        self._node_id: bytes = node_id
        self._epoch = epoch
        self._round_num = round_num

        self._event_system: EventSystem = event_system
        self._data_factory: DataFactory = data_factory
        self._vote_factory: VoteFactory = vote_factory
        self._data_pool = data_pool
        self._vote_pool = vote_pool

        self._logger = logging.getLogger(node_id.hex())

        self._data_verifier: DataVerifier = None

        self._candidate_id: bytes = None
        self._messages: RoundMessages = RoundMessages(epoch)

        self._is_voted = False
        self._is_ended = False
        self._is_started = False

    @property
    def result_id(self):
        result = self._messages.result
        if result and result.is_real():
            return result.id
        else:
            return None

    async def round_start(self):
        self._is_started = True
        self._data_verifier = await self._data_factory.create_data_verifier()

        await self._new_unreal_datums()
        await self._new_real_data_if_proposer()
        await self._vote_if_real_data_exist()

    async def receive_data(self, data: Data):
        self._messages.add_data(data)
        await self._update_result()
        await self._vote_if_available(data)

    async def receive_vote(self, vote: Vote):
        self._messages.add_vote(vote)
        await self._update_result()

    async def _raise_broadcast_data(self, data):
        self._event_system.simulator.raise_event(
            BroadcastDataEvent(
                data=data
            )
        )
        self._event_system.simulator.raise_event(
            ReceiveDataEvent(
                data=data
            )
        )

    async def _raise_broadcast_vote(self, vote: Vote):
        self._event_system.simulator.raise_event(
            BroadcastVoteEvent(
                vote=vote)
        )
        self._event_system.simulator.raise_event(
            ReceiveVoteEvent(
                vote=vote
            )
        )

    async def _raise_round_end(self, result: Data):
        if result.is_real():
            new_candidate = result
            round_end = RoundEndEvent(
                is_success=True,
                epoch_num=self._epoch.num,
                round_num=self._round_num,
                candidate_id=new_candidate.id,
                commit_id=new_candidate.prev_id
            )
        else:
            round_end = RoundEndEvent(
                is_success=False,
                epoch_num=self._epoch.num,
                round_num=self._round_num,
                candidate_id=None,
                commit_id=None
            )
        self._event_system.simulator.raise_event(round_end)

    async def _new_unreal_datums(self):
        none_data = await self._data_factory.create_none_data(epoch_num=self._epoch.num,
                                                              round_num=self._round_num,
                                                              proposer_id=self._epoch.get_proposer_id(self._round_num))
        self._messages.add_data(none_data)

        lazy_data = await self._data_factory.create_lazy_data(epoch_num=self._epoch.num,
                                                              round_num=self._round_num,
                                                              proposer_id=self._epoch.get_proposer_id(self._round_num))
        self._messages.add_data(lazy_data)

    async def _new_real_data_if_proposer(self):
        try:
            self._epoch.verify_proposer(self._node_id, self._round_num)
        except InvalidProposer:
            pass
        else:
            candidate_data = self._data_pool.get_data(self._candidate_id)
            candidate_votes = self._vote_pool.get_votes(candidate_data.epoch_num, candidate_data.round_num)
            candidate_votes = {vote.voter_id: vote for vote in candidate_votes if vote.data_id == self._candidate_id}
            candidate_votes = tuple(candidate_votes[voter] if voter in candidate_votes else None
                                    for voter in self._epoch.voters)

            new_data = await self._data_factory.create_data(
                data_number=candidate_data.number + 1,
                prev_id=self._candidate_id,
                epoch_num=self._epoch.num,
                round_num=self._round_num,
                prev_votes=candidate_votes
            )
            await self._raise_broadcast_data(new_data)

    async def _update_result(self):
        if not self._messages.result or not self._messages.result.is_determinative():
            self._messages.update()
        if not self._messages.result:
            return

        if self._is_ended:
            return
        await self._raise_round_end(self._messages.result)
        self._is_ended = True

    async def _vote_if_real_data_exist(self):
        first_real_data = self._messages.first_real_data
        if first_real_data:
            await self._verify_and_broadcast_vote(first_real_data)
            await self._update_result()
            self._is_voted = True

    async def _vote_if_available(self, data: Data):
        if not self._is_started:
            return
        if self._is_ended:
            return
        if self._is_voted:
            return

        await self._verify_and_broadcast_vote(data)
        self._is_voted = True

    async def _verify_and_broadcast_vote(self, data):
        if await self._verify_data(data):
            vote = await self._vote_factory.create_vote(data_id=data.id,
                                                        commit_id=self._candidate_id,
                                                        epoch_num=self._epoch.num,
                                                        round_num=self._round_num)
        else:
            vote = await self._vote_factory.create_none_vote(epoch_num=self._epoch.num,
                                                             round_num=self._round_num)
        await self._raise_broadcast_vote(vote)

    async def _verify_data(self, data):
        if data.proposer_id == self._node_id:
            return True
        if self._candidate_id != data.prev_id:
            return False
        candidate_data = self._data_pool.get_data(self._candidate_id)
        if candidate_data.number + 1 != data.number:
            return False
        if data.is_lazy():
            return False
        try:
            await self._data_verifier.verify(data)
        except Exception as e:
            return False
        else:
            return True
