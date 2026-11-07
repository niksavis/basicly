# Voter stances

Give each voter a different stance so the votes are independent. Pick an odd number: 3, or 5 when the decision is hard to undo. The script refuses an even number.

| Stance | The voter argues from |
| --- | --- |
| user | The person who uses the result: speed, clarity, fewer steps. |
| safety | What breaks, leaks or cannot be undone. Poka-yoke first. |
| maintainer | The cost to build, test and keep the option for years. |
| skeptic | Why the favoured option fails. The voter must name the strongest counter-case. |
| evidence | Only measured or sourced facts. A claim with no source does not count. |
| vendor | Current vendor and community practice, read from live docs. |

Brief each voter with [the swarm brief](../../swarm/assets/brief.md). Its lens is the stance. Its json block holds `vote`, `reason` and `strongest_counter`. The vote must be one option label exactly as written; the script refuses any other.
