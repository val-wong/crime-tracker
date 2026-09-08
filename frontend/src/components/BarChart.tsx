// A small, dependency-free, inherently-accessible bar chart: each row
// is a real table row with a visible numeric label -- the bar itself
// is a decorative background, never the only way a value is conveyed
// (docs/product.md item 13: "color is not the sole way information is
// conveyed"). Bars are chosen over a pie chart per docs/product.md V1
// instructions ("do not use pie charts if another chart communicates
// the data more clearly") -- comparing magnitudes is easier to read as
// bar length than as wedge angle, especially with many categories.

export interface BarChartItem {
  label: string;
  value: number;
}

interface BarChartProps {
  title: string;
  items: BarChartItem[];
  valueLabel: string;
  emptyMessage?: string;
}

export default function BarChart({ title, items, valueLabel, emptyMessage }: BarChartProps) {
  if (items.length === 0) {
    return (
      <p className="bar-chart__empty">{emptyMessage ?? "No data for the selected filters."}</p>
    );
  }

  const max = Math.max(...items.map((item) => item.value), 1);

  return (
    <table className="bar-chart">
      <caption className="bar-chart__caption">{title}</caption>
      <thead>
        <tr>
          <th scope="col">Label</th>
          <th scope="col">{valueLabel}</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.label}>
            <th scope="row" className="bar-chart__label">
              {item.label}
            </th>
            <td className="bar-chart__cell">
              <span
                className="bar-chart__bar"
                style={{ width: `${Math.max((item.value / max) * 100, 2)}%` }}
                aria-hidden="true"
              />
              <span className="bar-chart__value">{item.value.toLocaleString()}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
