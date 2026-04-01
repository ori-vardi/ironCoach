import { Component } from 'react'

export default class ErrorBoundary extends Component {
  state = { error: null }
  static getDerivedStateFromError(error) { return { error } }
  render() {
    if (this.state.error) {
      return (
        <div style={{ padding: 32, textAlign: 'center' }}>
          <h2>Something went wrong</h2>
          <p className="text-dim">{this.state.error.message}</p>
          <button className="btn btn-outline" onClick={() => this.setState({ error: null })}>Try Again</button>
        </div>
      )
    }
    return this.props.children
  }
}
