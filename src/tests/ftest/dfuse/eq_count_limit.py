"""
  (C) Copyright 2026 Hewlett Packard Enterprise Development LP

  SPDX-License-Identifier: BSD-2-Clause-Patent
"""
import re

from apricot import TestWithServers
from dfuse_utils import get_dfuse, start_dfuse

# Inside CaRT's [18, 128] clamp, so dfuse only reports it if daos_init() honours CRT_CTX_NUM.
CTX_LIMIT = 20
# CRT_CTX_NUM outside the clamp, and the limit it must give
CLAMPED_LIMITS = ((4, 18), (9999, 128))
# Above the clamp, so it can never fit
EQ_COUNT_PROBE = 200
LIMIT_RE = re.compile(r"limit (\d+), (\d+) in use")


class DfuseEqCountLimit(TestWithServers):
    """Verify dfuse refuses an --eq-count above the per-process context limit.

    :avocado: recursive
    """

    def _start(self, dfuse, eq_count):
        """Start dfuse with eq_count event queues.

        Args:
            dfuse (Dfuse): the dfuse instance to start
            eq_count (int): number of event queues to request

        Returns:
            tuple: (bool, str) whether dfuse started, and its stdout
        """
        results = []
        dfuse.eq_count.update(eq_count)
        # leave one fuse thread, so only the event queue guard can refuse the request
        dfuse.thread_count.update(eq_count + 1)
        dfuse.run(check=False, mount_callback=results.append)
        return results[0].passed, "\n".join(results[0].all_stdout.values())

    def _probe(self, dfuse, ctx_num):
        """Read the context limit dfuse reports when refusing EQ_COUNT_PROBE event queues.

        Args:
            dfuse (Dfuse): the dfuse instance to start
            ctx_num (int): value of CRT_CTX_NUM

        Returns:
            tuple: (int, int) the context limit and the contexts in use
        """
        self.log_step(f"Start dfuse with CRT_CTX_NUM={ctx_num}, expecting it to be refused")
        dfuse.env["CRT_CTX_NUM"] = str(ctx_num)
        passed, out = self._start(dfuse, EQ_COUNT_PROBE)
        self.assertFalse(passed, f"dfuse accepted --eq-count={EQ_COUNT_PROBE}")
        match = LIMIT_RE.search(out)
        self.assertIsNotNone(match, f"no context limit in dfuse output: {out}")
        return int(match.group(1)), int(match.group(2))

    def test_eq_count_limit(self):
        """Jira ID: DAOS-XXXXX.

        Test Description:
            daos_init() must honour CRT_CTX_NUM, and dfuse must refuse an --eq-count that cannot
            fit in the network contexts the process has left and accept one that exactly fits.
        Use cases:
            Start dfuse with CRT_CTX_NUM outside CaRT's range and verify the limit is clamped.
            Start dfuse with an --eq-count above the limit and verify it reports CRT_CTX_NUM.
            Start dfuse with one event queue more than fits and verify it is refused.
            Start dfuse with exactly as many event queues as fit and verify it mounts.
        :avocado: tags=all,pr,daily_regression
        :avocado: tags=vm
        :avocado: tags=dfuse
        :avocado: tags=DfuseEqCountLimit,test_eq_count_limit
        """
        pool = self.get_pool(connect=False)
        container = self.get_container(pool)

        # one client: another host could report a different limit
        dfuse = get_dfuse(self, self.hostlist_clients[:1])
        dfuse.update_params(pool=pool.identifier, cont=container.identifier)

        for ctx_num, expected in CLAMPED_LIMITS:
            limit, _ = self._probe(dfuse, ctx_num)
            self.assertEqual(limit, expected, f"wrong limit for CRT_CTX_NUM={ctx_num}")

        limit, in_use = self._probe(dfuse, CTX_LIMIT)
        self.assertEqual(limit, CTX_LIMIT, "daos_init() did not honour CRT_CTX_NUM")
        headroom = limit - in_use
        self.log.info("context limit %d, %d in use, headroom %d", limit, in_use, headroom)

        self.log_step(f"Start dfuse with --eq-count={headroom + 1}, expecting it to be refused")
        passed, _ = self._start(dfuse, headroom + 1)
        self.assertFalse(passed, f"dfuse accepted --eq-count={headroom + 1}")

        self.log_step(f"Start dfuse with --eq-count={headroom}, expecting it to mount")
        dfuse.eq_count.update(headroom)
        dfuse.thread_count.update(headroom + 1)
        start_dfuse(self, dfuse)

        self.log.info("Test passed")
