import { useActions, useValues } from 'kea'

import { IconDatabase, IconRefresh, IconWarning } from '@posthog/icons'
import { LemonBanner, LemonDivider, LemonSelect, LemonSwitch } from '@posthog/lemon-ui'

import { LemonField } from 'lib/lemon-ui/LemonField'

import { SceneSection } from '~/layout/scenes/components/SceneSection'

import { endpointLogic } from './endpointLogic'

interface EndpointConfigurationProps {
    tabId: string
}

type CacheAgeOption = number | null
type SyncFrequency = 'hourly' | 'daily' | 'weekly'

const CACHE_AGE_OPTIONS: { value: CacheAgeOption; label: string }[] = [
    { value: null, label: 'Default caching behavior' },
    { value: 300, label: '5 minutes' },
    { value: 900, label: '15 minutes' },
    { value: 1800, label: '30 minutes' },
    { value: 3600, label: '1 hour' },
    { value: 10800, label: '3 hours' },
    { value: 86400, label: '1 day' },
    { value: 259200, label: '3 days' },
]
const SYNC_FREQUENCY_OPTIONS: { value: SyncFrequency; label: string }[] = [
    { value: 'hourly', label: 'Hourly' },
    { value: 'daily', label: 'Daily' },
    { value: 'weekly', label: 'Weekly' },
]

export function EndpointConfiguration({ tabId }: EndpointConfigurationProps): JSX.Element {
    const { setCacheAge, setSyncFrequency, setIsMaterialized } = useActions(endpointLogic({ tabId }))
    const {
        endpoint,
        cacheAge,
        syncFrequency,
        isMaterialized: localIsMaterialized,
    } = useValues(endpointLogic({ tabId }))

    if (!endpoint) {
        return <></>
    }

    const canMaterialize = endpoint.materialization?.can_materialize ?? false
    const isMaterialized = localIsMaterialized ?? endpoint.is_materialized
    const materializationStatus = endpoint.materialization?.status
    const lastMaterializedAt = endpoint.materialization?.last_materialized_at

    const handleToggleMaterialization = (): void => {
        setIsMaterialized(!isMaterialized)
    }

    return (
        <SceneSection title="Configure this endpoint">
            <div className="flex flex-col gap-4 max-w-2xl">
                <LemonField.Pure
                    label="Cache age"
                    info="Cache age defines how long your endpoint will return cached results before running the query again
                    and refreshing the results."
                >
                    <LemonSelect value={cacheAge} onChange={setCacheAge} options={CACHE_AGE_OPTIONS} />
                </LemonField.Pure>

                <div className="space-y-4">
                    <div className="flex items-center justify-between">
                        <div>
                            <h3 className="text-base font-semibold mb-1">Query materialization</h3>
                            <p className="text-xs text-secondary">
                                Pre-compute and store query results in S3 for better query performance.
                            </p>
                        </div>
                        <LemonSwitch
                            checked={isMaterialized}
                            onChange={handleToggleMaterialization}
                            disabled={!canMaterialize && !isMaterialized}
                            bordered
                        />
                    </div>

                    {!canMaterialize && !isMaterialized && endpoint.materialization?.reason && (
                        <LemonBanner type="warning">
                            <div className="flex items-center gap-2">
                                <IconWarning className="text-lg" />
                                <span>{endpoint.materialization.reason}</span>
                            </div>
                        </LemonBanner>
                    )}

                    {isMaterialized && (
                        <div className="space-y-3 p-4 bg-accent-3000 border border-border rounded">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <IconDatabase className="text-lg" />
                                    <span className="font-medium">Materialization status</span>
                                </div>
                                <span className="text-xs px-2 py-1 bg-success-highlight text-success rounded">
                                    {materializationStatus || 'Active'}
                                </span>
                            </div>

                            {lastMaterializedAt && (
                                <div className="flex items-center gap-2 text-xs text-secondary">
                                    <IconRefresh className="text-base" />
                                    <span>Last materialized: {new Date(lastMaterializedAt).toLocaleString()}</span>
                                </div>
                            )}

                            {endpoint.materialization?.error && (
                                <LemonBanner type="error" className="mt-2">
                                    {endpoint.materialization.error}
                                </LemonBanner>
                            )}
                        </div>
                    )}

                    {canMaterialize && (
                        <LemonField.Pure label="Sync frequency" info="How often the materialized view is refreshed">
                            <LemonSelect
                                value={syncFrequency || 'daily'}
                                onChange={setSyncFrequency}
                                options={SYNC_FREQUENCY_OPTIONS}
                            />
                        </LemonField.Pure>
                    )}
                </div>
            </div>
            <LemonDivider />
        </SceneSection>
    )
}
