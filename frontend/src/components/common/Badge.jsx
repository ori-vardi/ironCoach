export default function Badge({ type, text }) {
  return <span className={`badge badge-${type}`}>{text || type}</span>
}
