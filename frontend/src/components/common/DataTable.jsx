export default function DataTable({ columns, rows, onRowClick, rowKey, className = '' }) {
  return (
    <div className="table-scroll">
      <table className={`data-table ${className}`}>
        <thead>
          <tr>
            {columns.map((col, i) => (
              <th key={i}>{col.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr
              key={rowKey ? (typeof rowKey === 'function' ? rowKey(row, ri) : row[rowKey]) : ri}
              className={onRowClick ? 'clickable' : ''}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
            >
              {columns.map((col, ci) => (
                <td key={ci} style={col.style?.(row)}>
                  {col.render ? col.render(row) : row[col.key]}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
