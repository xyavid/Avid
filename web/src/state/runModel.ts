/**
 * Which model and reasoning level this run uses — both chosen by the user in the input area, with
 * no "follow settings" option. Settings only supply the candidate and level lists; a stored choice
 * that no longer appears there is dropped, so a run never carries an undeclared ref or level.
 */

import { useCallback, useEffect, useState } from 'react'

import type { ModelCandidate } from '../api/types'

const MODEL_KEY = 'avid.run.model'
const EFFORT_KEY = 'avid.run.effort'

function read(key: string): string | null {
  try {
    const raw = localStorage.getItem(key)
    return raw === null || raw === '' ? null : raw
  } catch {
    return null
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // Storage failure (private mode) only affects remembering, not how this run goes
  }
}

/** The input area's pair of choices: model + this run's reasoning level (level may be unset). */
export type RunChoice = {
  model: string | null
  effort: string | null
  chooseModel: (ref: string) => void
  chooseEffort: (level: string | null) => void
  /** Levels declared by the current model (empty = the model declares none; hide the picker). */
  efforts: string[]
}

export function useRunChoice(candidates: ModelCandidate[]): RunChoice {
  const [model, setModel] = useState<string | null>(() => read(MODEL_KEY))
  const [effort, setEffort] = useState<string | null>(() => read(EFFORT_KEY))

  const current = candidates.find((candidate) => candidate.ref === model)
  const efforts = current?.reasoning_efforts ?? []

  // Do not touch the stored choice until candidates arrive, or mount would clear the user's pick
  useEffect(() => {
    if (candidates.length === 0) return
    setModel((now) => {
      if (now !== null && !candidates.some((candidate) => candidate.ref === now)) {
        write(MODEL_KEY, null)
        return null
      }
      return now
    })
  }, [candidates])

  // Level follows the model: a switch or a removed level invalidates the stored one
  useEffect(() => {
    if (effort === null || candidates.length === 0) return
    if (!efforts.includes(effort)) {
      write(EFFORT_KEY, null)
      setEffort(null)
    }
  }, [candidates.length, effort, efforts])

  const chooseModel = useCallback((ref: string) => {
    setModel(ref)
    write(MODEL_KEY, ref)
  }, [])

  const chooseEffort = useCallback((level: string | null) => {
    setEffort(level)
    write(EFFORT_KEY, level)
  }, [])

  return { model, effort, chooseModel, chooseEffort, efforts }
}
