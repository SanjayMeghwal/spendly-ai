import { afterEach, describe, expect, it, vi } from 'vitest'
import { askAdvisor } from './advisor'

const fetchMock = vi.fn()

vi.stubGlobal('fetch', fetchMock)

afterEach(() => {
  fetchMock.mockReset()
})

describe('askAdvisor', () => {
  it('posts the question and returns structured advice', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          advice: 'Reduce dining out.',
          recommended_actions: ['Set a dining budget.'],
          warnings: ['This month is already above target.'],
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    )

    await expect(askAdvisor('How can I spend less?')).resolves.toEqual({
      advice: 'Reduce dining out.',
      recommended_actions: ['Set a dining budget.'],
      warnings: ['This month is already above target.'],
    })
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/agents/advice'),
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ question: 'How can I spend less?' }),
      }),
    )
  })
})
