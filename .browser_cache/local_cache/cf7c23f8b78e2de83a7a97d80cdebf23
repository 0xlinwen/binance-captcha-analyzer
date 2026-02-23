if ('serviceWorker' in navigator) {
  navigator.serviceWorker
    .register('/pwa-api-cache@0.0.5.js', {
      scope: '/',
    })
    .then(registration => {
      if (registration.installing) {
        console.log('Service worker installing')
      } else if (registration.waiting) {
        console.log('Service worker installed')
      } else if (registration.active) {
        console.log('Service worker active')
      }
    })
    .catch(error => {
      console.error(`Service worker registration failed: ${error}`)
    })
  const pickData = res => {
    if (res.success) {
      return res.data
    }
  }
  const keyPickDataObject = {
    userBaseInfo: pickData,
    prefreUserConfig: pickData,
    userProfile: pickData,
    userUserInfo: pickData,
    useUserPayFiatListCurrency: res => {
      const result = pickData(res)
      return result ? result.fiatList : undefined
    },
    businessKeys: res => {
      const result = pickData(res)
      return result
        ? {
            keys: result.restriction.keys,
            allowedKeys: result.restriction.allowedKeys,
          }
        : undefined
    },
    useMarginAccountExist: pickData,
    profile: pickData,
    useTodoList: pickData,
    useUserComplianceStatus: res => {
      const result = pickData(res)
      return result ? result.userComplianceStatus : undefined
    },
    useUserExtraInfo: pickData,
    useUserFuturesAccountInfo: pickData,
    useUserKycStatusInfo: pickData,
    accountUserQuoteAssetGet: res => {
      const result = pickData(res)
      return result ? result.configValue || 'BTC' : undefined
    },
    userTaskChallengeList: pickData,
    userTradeStatus: pickData,
    'useLanguageList--': res => {
      const data = pickData(res)
      if (!data) {
        return undefined
      }
      const language = {}
      data.match.forEach(item => {
        language[item.target] = item.name
      })
      return Object.entries(language).sort((a, b) => a[0].localeCompare(b[0]))
    },
  }
  const dataDeepMatch = (oldData, newData) => {
    if (typeof oldData !== typeof newData) {
      return false
    }
    if (typeof oldData === 'object' && oldData !== null && newData !== null) {
      return Object.keys(oldData).every(key => dataDeepMatch(oldData[key], newData[key]))
    }
    return oldData === newData
  }

  navigator.serviceWorker.addEventListener('message', event => {
    const { cacheKey, payload } = event.data
    const pickDataFunction = keyPickDataObject[cacheKey]
    const pickedData = pickDataFunction(payload)

    // 请求失败埋点
    if (typeof pickedData === 'undefined') {
      __COMMON_WIDGET_CACHE_STORE__.track('ComponentShow', {
        pageName: 'sync_data_picked_data_undefined',
        elementId: cacheKey,
      })
      return
    }

    // 缓存失败埋点
    if (typeof __COMMON_WIDGET_CACHE_STORE__.state[cacheKey] === 'undefined') {
      __COMMON_WIDGET_CACHE_STORE__.setState(cacheKey, pickedData)
      __COMMON_WIDGET_CACHE_STORE__.track('ComponentShow', {
        pageName: 'sync_data_state_undefined',
        elementId: cacheKey,
      })
      return
    }
    // object match
    if (!dataDeepMatch(__COMMON_WIDGET_CACHE_STORE__.state[cacheKey], pickedData)) {
      __COMMON_WIDGET_CACHE_STORE__.setState(cacheKey, pickedData)
      __COMMON_WIDGET_CACHE_STORE__.track('ComponentShow', {
        pageName: 'sync_data_object_noMatch',
        elementId: cacheKey,
      })
      return
    }
    __COMMON_WIDGET_CACHE_STORE__.track('ComponentShow', {
      pageName: 'keep_data',
      elementId: cacheKey,
    })
  })
}

// 加一个注入sevice worker的开关
// https://git.toolsfdg.net/fe/binance-cookie-manager/blob/master/src/libs/logger.ts#L34
// 通过cache promise store开放埋点口子。
// trade
// 发送了请求
// 命中缓存和非命中缓存
// 命中缓存是否更新
