import { Fragment } from "react";
import { formatDate, formatFullDateTime } from "../../api";
import type { RaceSummary } from "../../types";
import { RaceDetailView } from "./RaceDetailView";
import { formatCourse, formatPercent, raceStatusLabel } from "./helpers";

export function RaceRow({
  race,
  open,
  onToggle,
}: {
  race: RaceSummary;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <Fragment>
      <tr className="row-clickable" onClick={onToggle}>
        <td>{formatDate(race.race_date)}</td>
        <td>{race.venue}</td>
        <td>{race.race_number}</td>
        <td>{formatCourse(race.track_type, race.distance)}</td>
        <td>{race.race_name || "-"}</td>
        <td>{formatFullDateTime(race.start_time)}</td>
        <td>{race.entry_count}</td>
        <td>
          <span className={`race-status ${race.finished ? "finished" : "unfinished"}`}>
            {raceStatusLabel(race.finished)}
          </span>
        </td>
        <td>
          {race.top_prediction
            ? `${race.top_prediction.horse_number}番 ${
                race.top_prediction.horse_name ?? ""
              } (${formatPercent(race.top_prediction.score)})`
            : "-"}
        </td>
        <td>{race.bet_count > 0 ? `${race.bet_count}件` : ""}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={10}>
            <RaceDetailView raceId={race.id} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}
