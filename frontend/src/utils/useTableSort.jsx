import { useState, useMemo } from 'react'

/**
 * Hook for sortable tables.
 * @param {Array} data - array of row objects
 * @param {Object} colMap - { colKey: row => sortValue } mapping
 * @param {string} defaultCol - initial sort column key
 * @param {string} defaultDir - 'asc' or 'desc'
 * @returns {{ sorted, sortCol, sortDir, handleSort, sortArrow }}
 */
export default function useTableSort(data, colMap, defaultCol = 'date', defaultDir = 'desc') {
  const [sortCol, setSortCol] = useState(defaultCol)
  const [sortDir, setSortDir] = useState(defaultDir)

  const sorted = useMemo(() => {
    const getter = colMap[sortCol] || colMap[defaultCol]
    if (!getter) return data
    const arr = [...data]
    arr.sort((a, b) => {
      const va = getter(a)
      const vb = getter(b)
      if (va < vb) return sortDir === 'asc' ? -1 : 1
      if (va > vb) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return arr
  }, [data, colMap, sortCol, sortDir, defaultCol])

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortCol(col)
      setSortDir('desc')
    }
  }

  const sortArrow = (col) => {
    if (sortCol !== col) return null
    return <span className="sort-arrow">{sortDir === 'asc' ? '\u25B2' : '\u25BC'}</span>
  }

  return { sorted, sortCol, sortDir, handleSort, sortArrow }
}
