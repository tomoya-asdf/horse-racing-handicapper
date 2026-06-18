import type { ModelFeatureGroup } from "../../types";
import type { FeatureForm } from "./helpers";

export function FeatureSelect({
  groups,
  featureForm,
  onToggle,
  onGroup,
}: {
  groups: ModelFeatureGroup[];
  featureForm: FeatureForm;
  onToggle: (name: string) => void;
  onGroup: (names: string[], value: boolean) => void;
}) {
  return (
    <div className="feature-select">
      {groups.map((group) => {
        const names = group.features.map((f) => f.name);
        return (
          <div key={group.group} className="feature-select-group">
            <div className="feature-select-head">
              <h3>{group.group}</h3>
              <div className="feature-select-actions">
                <button type="button" onClick={() => onGroup(names, true)}>
                  全選択
                </button>
                <button type="button" onClick={() => onGroup(names, false)}>
                  全解除
                </button>
              </div>
            </div>
            <div className="feature-checkboxes">
              {group.features.map((feature) => (
                <label key={feature.name} className="feature-checkbox">
                  <input
                    type="checkbox"
                    checked={Boolean(featureForm[feature.name])}
                    onChange={() => onToggle(feature.name)}
                  />
                  <span>
                    {feature.label}
                    {feature.categorical && <span className="muted"> (カテゴリ)</span>}
                    {feature.missing_rate != null && (
                      <span className="muted feature-missing">
                        {" "}
                        欠損{(feature.missing_rate * 100).toFixed(0)}%
                      </span>
                    )}
                  </span>
                </label>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}
