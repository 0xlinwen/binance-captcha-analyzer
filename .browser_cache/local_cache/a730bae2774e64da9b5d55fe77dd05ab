'use strict'

var warningIcon =
  'data:image/png;base64,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'

const closeIcon =
  'data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMjQiIGhlaWdodD0iMjQiIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj4KPHBhdGggZD0iTTYuNjk2NTkgNC41NzUzOEw0LjU3NTI3IDYuNjk2N0w5Ljg3ODU3IDEyTDQuNTc1MjcgMTcuMzAzM0w2LjY5NjU5IDE5LjQyNDZMMTEuOTk5OSAxNC4xMjEzTDE3LjMwMzIgMTkuNDI0NkwxOS40MjQ1IDE3LjMwMzNMMTQuMTIxMiAxMkwxOS40MjQ1IDYuNjk2N0wxNy4zMDMyIDQuNTc1MzhMMTEuOTk5OSA5Ljg3ODY4TDYuNjk2NTkgNC41NzUzOFoiIGZpbGw9IiM5MjlBQTUiLz4KPC9zdmc+Cg=='

// a function to execute a callback after polling a condition for a certain time
function pollAndWaitCondition(callback, conditionFunc, interval = 100, timeout = 5000, extraInfo) {
  extraInfo = extraInfo || ''
  let timer = null
  let startTime = Date.now()
  function poll() {
    if (conditionFunc()) {
      console.log('pollCondition wait success, conditionFunc return true, extraInfo:', extraInfo)
      clearInterval(timer)
    } else if (Date.now() - startTime > timeout) {
      console.log('pollCondition timeout, fallback to execute callback, extraInfo:', extraInfo)
      callback()
      clearInterval(timer)
    }
  }
  timer = setInterval(poll, interval)
}

function shouldShowJPTopBar({ displayAfterHours = 12 } = {}) {
  const { region = '', locale = '', isLoggedIn } = bnvelidate || {}
  const timer = Number(localStorage.getItem('JPTopBarTimer')) || 0

  if (isLoggedIn) {
    return false
  }
  if (Date.now() - timer < displayAfterHours * 60 * 60 * 1000) {
    return false
  }
  if (region.toLowerCase() === 'jp' && ['ja', 'en-jp'].includes(locale && locale.toLowerCase())) {
    return true
  }
  return false
}

function getCookie(name) {
  const value = `; ${document.cookie}`
  const parts = value.split(`; ${name}=`)
  if (parts.length === 2) return parts.pop().split(';').shift()
}

function deprecateCookieWithDomain(name, domain) {
  if (!domain) {
    domain = window.location.hostname
  }
  var cookies = document.cookie.split('; ')
  for (var c = 0; c < cookies.length; c++) {
    var d = domain.split('.')
    var currentName = encodeURIComponent(cookies[c].split(';')[0].split('=')[0])
    if (currentName !== name) {
      continue
    }
    // console.log('matched cookie', name);
    while (d.length > 0) {
      var cookieBase = currentName + '=; expires=Thu, 01-Jan-1970 00:00:01 GMT; domain=' + d.join('.') + ' ;path='
      // Currently we traverse the path starts from current location
      var p = location.pathname.split('/')
      document.cookie = cookieBase + '/'
      while (p.length > 0) {
        document.cookie = cookieBase + p.join('/')
        p.pop()
      }
      d.shift()
    }
  }
}

function getUrl(url) {
  const arr = window.location.host.split('.')
  arr.shift()
  arr.unshift('www')
  const newHost = /localhost/.test(window.location.host) ? window.location.host : arr.join('.')
  return `https://${newHost}/${url}`
}

function getDisclaimer({ id, msg, isHTMLMsg, redirectUrl, isOpenNewPage, btnText, prefixIconSvg, closeIconSvg }) {
  var linkWrapper
  var contentWrapper
  var prefixIconWrapper
  var msgEle
  var btnSuffixWrapper
  var closeIconWrapper
  var btn
  var rightContent

  if (redirectUrl) {
    linkWrapper = document.createElement('a')
    linkWrapper.style.textDecorationColor = 'transparent'
    linkWrapper.href = redirectUrl
    if (isOpenNewPage) {
      linkWrapper.target = '_blank'
    }
  }

  contentWrapper = document.createElement('div')
  contentWrapper.style.display = 'flex'
  contentWrapper.style.alignItems = 'start'
  contentWrapper.style.justifyContent = 'center'
  contentWrapper.style.maxWidth = '1200px'
  contentWrapper.style.margin = '0 auto'
  contentWrapper.style.fontSize = '14px'

  rightContent = document.createElement('div')
  rightContent.style.margin = '0 8px 0 8px'

  if (prefixIconSvg) {
    prefixIconWrapper = document.createElement('div')
    prefixIconWrapper.innerHTML = prefixIconSvg
  }

  if (closeIconSvg) {
    closeIconWrapper = document.createElement('img')
    closeIconWrapper.src = closeIconSvg
    closeIconWrapper.style.height = '20px'
    closeIconWrapper.style.width = '20px'
    closeIconWrapper.style.cursor = 'pointer'
    closeIconWrapper.onclick = function () {
      localStorage.setItem('JPTopBarTimer', Date.now())
      var bar = document.getElementById(id || 'disclaimer-top-bar')
      bar && bar.remove()
    }
  }

  msgEle = document.createElement('div')
  if (isHTMLMsg) {
    msgEle.innerHTML = msg
  } else {
    msgEle.innerText = msg
  }
  msgEle.style.color = '#EAECEF'
  msgEle.style.fontSize = '14px'
  msgEle.style.lineHeight = '20px'
  msgEle.style.textDecorationColor = 'transparent'

  if (btnText) {
    btn = document.createElement('span')
    btn.innerText = btnText
    btn.style.color = '#F0B90B'
    btn.style.lineHeight = '20px'
    btn.style.marginLeft = '4px'

    msgEle.appendChild(btn)
  }

  rightContent.appendChild(msgEle)

  if (prefixIconWrapper) contentWrapper.appendChild(prefixIconWrapper)
  if (linkWrapper) {
    linkWrapper.appendChild(rightContent)
    contentWrapper.appendChild(linkWrapper)
  } else {
    contentWrapper.appendChild(rightContent)
  }
  if (closeIconWrapper) contentWrapper.appendChild(closeIconWrapper)
  // if (btnSuffixWrapper) contentWrapper.appendChild(btnSuffixWrapper)

  // if (linkWrapper) {
  //   linkWrapper.appendChild(contentWrapper)
  //   return linkWrapper
  // } else {
  //   return contentWrapper
  // }
  return contentWrapper
}

function createSticky(config) {
  var topBar
  var contentEle = getDisclaimer(config)

  topBar = document.createElement('div')
  topBar.style.width = '100%'
  topBar.style.boxSizing = 'border-box'
  topBar.style.padding = '12px 16px'
  topBar.style.backgroundColor = '#2B3139'
  topBar.style.position = 'sticky'
  topBar.style.top = '0px'
  // notification z-index is 1300, so we should set this z-index lower than notification if we don't want to cover it
  topBar.style.zIndex = config.topBarZIndex || '9999'
  topBar.style.fontWeight = config.topBarFontWeight || '400'
  topBar.id = config.id || 'disclaimer-top-bar'

  topBar.appendChild(contentEle)

  var isCommonWidget = document.getElementById('__APP_HEADER')
  var appEle = isCommonWidget ? document.body : document.getElementById('__APP')

  if (appEle) {
    var appChild = appEle.childNodes[0]
    appEle.insertBefore(topBar, appChild)
  }
}

// function disclaimerTopBar() {
//   const dataConfig = {
//     msg: 'La inversión en criptoactivos no está regulada, puede no ser adecuada para inversores minoristas y perderse la totalidad del importe invertido. Es importante leer y comprender los riesgos de esta inversión que se explican detalladamente',
//     btnText: 'en esta ubicación.',
//     redirectUrl: getUrl('es/support/faq/1f045f3170254817ad9226a3a0f2f42e'),
//     isOpenNewPage: true,
//   }
//   window._getDisclaimer = getDisclaimer
//   if (shouldShowDisclaimerTopBar()) {
//     createSticky(dataConfig)
//   }
// }

function jpComplianceTopBar() {
  const dataConfigEn = {
    msg: 'Crypto-assets & Services available to Japan residents will partially differ from those which you can see here. Please refer to it ',
    btnText: 'here.',
    redirectUrl: getUrl('en-JP/legal/tokens-and-products '),
    isOpenNewPage: true,
    closeIconSvg: closeIcon,
  }
  const dataConfigJp = {
    msg: '日本居住者が利用可能な暗号資産及びサービスは本ページ記載のものと一部異なります。',
    btnText: 'こちらのページをご確認ください。',
    redirectUrl: getUrl('ja/legal/tokens-and-products'),
    isOpenNewPage: true,
    closeIconSvg: closeIcon,
  }
  const { locale } = bnvelidate
  const dataConfig = locale === 'ja' ? dataConfigJp : dataConfigEn
  window._getDisclaimer = getDisclaimer
  if (shouldShowJPTopBar()) {
    createSticky(dataConfig)
  }
}

var SkipRedirectWhitelist = {
  accounts: ['/oauth'],
}

function checkSkipCompliance() {
  var domains = window.location.hostname.split('.')
  var thirdLevelDomain = domains[domains.length - 3]
  var skipPaths = SkipRedirectWhitelist[thirdLevelDomain] || []
  return skipPaths.some(function (path) {
    // FIXME: Web URL Migration Please use new getLanguage 4.2 (uikit-widget/src/GlobalJs/common.js:244)
    const currentPathWithoutLng = '/' + window.location.pathname.split('/').slice(2).join('/')
    return currentPathWithoutLng.startsWith(path)
  })
}

function isBlogPage() {
  // FIXME: Web URL Migration Please use new getLanguage 4.2 (uikit-widget/src/GlobalJs/common.js:259)
  const paths = window.location.pathname.split('/')
  if (paths && paths.length >= 3) {
    return paths[2] === 'blog'
  }
  return false
}

function getJsBridgeVersion() {
  if (window && window.navigator.userAgent && /BNC\/[\d\.]+/i.test(window.navigator.userAgent)) {
    return window.navigator.userAgent.match(/BNC\/[\d\.]+/i)[0].slice(4)
  }
  return ''
}

function getComplianceBannerHTML(bannerData) {
  const { text, links = [] } = bannerData
  if (!links.length) return text
  // traverse the links array, find every linkText which matchs the text, and wrap it with a <a> tag
  const res = []
  let i = 0
  let j = 0
  while (i < text.length) {
    if (text[i] === links[j].linkText[0]) {
      let k = 0
      while (k < links[j].linkText.length && text[i + k] === links[j].linkText[k]) {
        k++
      }
      if (k === links[j].linkText.length) {
        let targetIndex = j
        let redirectUrl = links[targetIndex].linkUrl
        if (!redirectUrl.startsWith('http')) {
          redirectUrl = bnvelidate.replaceUrl(
            `https://www.%suffixOrigin%/%locale%${redirectUrl.startsWith('/') ? '' : '/'}${redirectUrl}`,
          )
        }
        const aTagStr = `<a style="color: #F0B90B; text-decoration: underline;" target="_blank" href="${redirectUrl}" >${links[j].linkText}</a>`
        res.push(aTagStr)
        i += k
        j++
      } else {
        res.push(text[i])
        i++
      }
    } else {
      res.push(text[i])
      i++
    }
  }

  return res.join('')
}

function checkBannerComplianceAPI() {
  const isHybrid = Boolean(getJsBridgeVersion())
  if (window.__NEZHA_BRIDGE__ || isHybrid) {
    return
  }

  bnvelidate
    .postBNHTTP('/bapi/compliance/v1/friendly/compliance/market/banner-compliance-check', {})
    .then(function (res) {
      if (res && res.success && res.data && !res.data.pass) {
        const headBanner = res.data.headBanner || {}
        if (!headBanner.text) return
        const complianceBannerHTML = getComplianceBannerHTML(headBanner)

        const dataConfig = {
          id: 'global-banner-compliance-check',
          topBarZIndex: '1299',
          topBarFontWeight: 'bold',
          msg: complianceBannerHTML,
          isHTMLMsg: true,
          // closeIconSvg: closeIcon,
        }

        createSticky(dataConfig)
      }
    })
    .catch(function (err) {
      console.error('checkBannerComplianceAPI error = ', err)
    })
}

// Some old cookies should be deprecated
function executeDeprecatedCookiesTimer() {
  var deprecatedCookiesList = [
    { name: 'sajssdk_2015_cookie_access_test' },
    { name: 'sensorsdata_domain_test' },

    { name: 'fan-token-init-compliance' },
    { name: 'c2c-menu-ssbtc' },
    { name: 'c2c-menu-ssgc' },
    { name: 'c2c-menu-ssct' },
    { name: 'teamList-showByCard' },
    { name: 'sys_mob', domain: 'www.binance.com' },
  ]

  function executor() {
    deprecatedCookiesList.forEach(function (item) {
      deprecateCookieWithDomain(item.name, item.domain)
      // console.log('deprecate cookie: ', item.name)
    })
  }

  setTimeout(executor, 1000 * 5) // delay 5s to execute
}

const ONE_DAY = 60 * 60 * 1000 * 24

;(function () {
  var main = function () {
    executeDeprecatedCookiesTimer()

    checkBannerComplianceAPI()

    if (checkSkipCompliance()) return
    // deprecated es disclaimer
    // disclaimerTopBar()
    jpComplianceTopBar()

    hkPopup()

    var baseOverflowsdve1dfc
    var baseHeightsdve1dfc

    function lockScreen() {
      var html = document.getElementsByTagName('html')[0]
      baseOverflowsdve1dfc = html.style.overflowY
      baseHeightsdve1dfc = html.style.height
      html.style.overflowY = 'hidden'
      html.style.height = '100vh'
    }

    function unlockScreen() {
      var html = document.getElementsByTagName('html')[0]
      html.style.overflowY = baseOverflowsdve1dfc
      html.style.height = baseHeightsdve1dfc
    }

    function clearModal() {
      var node = document.getElementById('globalmodal-common')
      node && node.parentNode.removeChild(node)
      unlockScreen()
    }

    function fetchResource(url, opt = {}) {
      return fetch(url, { method: 'GET', mode: 'cors', ...opt }).then(function (result) {
        if (result.ok) return result.json()
        throw new Error(`get url:${url} resource fail`)
      })
    }

    function loadHeaderI18n(params, opt = {}) {
      const { basePath, namespace, locale = 'en', fallback = 'en' } = params
      if (!basePath || !namespace) throw TypeError('basePath and namespace must be defined')
      const i18nURL = `${basePath}/${locale}/${namespace}`
      return fetchResource(i18nURL, opt)
        .then(function (result) {
          if (!result || !Object.keys(result).length) throw new Error('fallback i18n')
          return result
        })
        .catch(function (err) {
          console.error(`query i18n fail: ${namespace} | ${locale}`, err)
          return typeof fallback === 'string' && fallback !== locale
            ? loadHeaderI18n({ basePath, namespace, locale: fallback, fallback: false }, opt)
            : Object.create(null)
        })
    }

    function getLocale(url = window.location.pathname) {
      const localeRegex = /^\/([a-z]{2}(-[A-Z]{2})?)/
      const matched = url.match(localeRegex)
      return matched && matched.length >= 2 ? matched[1] : 'en'
    }

    function getStaticUrl() {
      try {
        var url = new URL(document.currentScript.src)
        return url.origin
      } catch (e) {
        return 'https://bin.bnbstatic.com'
      }
    }

    function getI18nBasePath() {
      return [getStaticUrl(), '/api/i18n/-/web/cms'].join('')
    }

    function getGetHeaderI18n() {
      const { initialFrom } = bnvelidate

      if (initialFrom === 'header' || !initialFrom) {
        return new Promise(function (resolve) {
          const testLoadedKey = 'bn-pop-cancel'
          const isLoaded = bnvelidate.getHeaderI18n(testLoadedKey) !== testLoadedKey
          if (isLoaded) {
            resolve(bnvelidate.getHeaderI18n)
            return
          }
          loadHeaderI18n({ basePath: getI18nBasePath(), namespace: 'Navigation', locale: getLocale() }).then(function (
            i18n,
          ) {
            resolve(function (key) {
              return i18n[key] || key
            })
          })
        })
      }

      if (bnvelidate.loadHeaderI18n) {
        return bnvelidate
          .loadHeaderI18n()
          .then(function () {
            return new Promise(function (resolve) {
              setTimeout(resolve, 10)
            })
          })
          .then(function () {
            return bnvelidate.getHeaderI18n
          })
      } else {
        return loadHeaderI18n({ basePath: getI18nBasePath(), namespace: 'Navigation', locale: getLocale() }).then(
          function (i18n) {
            return function (key) {
              return i18n[key] || key
            }
          },
        )
      }
    }

    function showModal({
      modaltitle,
      modalcontent,
      modalimg,
      cancelContent,
      cancelBtnId,
      okContent,
      okBtnId,
      namespace = '',
      isForceOpen,
      handleOk,
      handleCancel,
      checkboxElem,
      displayInterval = 120 * 60 * 1000,
      afterDisplayIntervalCheck,
      getHeaderI18n,
    }) {
      const storageKey = 'kyc-poper' + namespace
      let timer = localStorage.getItem(storageKey)
      if (!isForceOpen) {
        if (timer) {
          if (Date.now() - timer < displayInterval) {
            return
          }
          localStorage.setItem(storageKey, Date.now().toString())
        } else {
          localStorage.setItem(storageKey, Date.now().toString())
        }
      }
      afterDisplayIntervalCheck && afterDisplayIntervalCheck()

      lockScreen()
      modalCode({
        modaltitle: getHeaderI18n(modaltitle),
        modalcontent: getHeaderI18n(modalcontent),
        modalimg: modalimg,
        cancelContent: getHeaderI18n(cancelContent),
        cancelBtnId,
        okContent: getHeaderI18n(okContent),
        okBtnId,
        handleOk,
        handleCancel,
        checkboxElem,
      })
    }

    function modalCode({
      modaltitle,
      modalcontent,
      modalimg,
      cancelContent,
      cancelBtnId,
      okContent,
      okBtnId,
      handleOk,
      handleCancel,
      checkboxElem,
    }) {
      var node = document.createElement('div')
      node.setAttribute('id', 'globalmodal-common')
      node.style.height = '100vh'
      node.style.boxSizing = 'border-box'
      node.style.width = '100%'
      node.style.position = 'fixed'
      node.style.top = '0px'
      node.style.right = '0px'
      node.style.left = '0px'
      node.style.bottom = '0px'
      node.style.zIndex = '10086'
      node.style.background = 'rgba(0, 0, 0, 0.55)'
      node.style.display = 'flex'
      node.style.alignItems = 'center'
      node.style.justifyContent = 'center'
      node.style.fontFamily = 'BinancePlex,Arial,sans-serif'
      var modal = document.createElement('div')
      modal.style.display = 'flex'
      modal.style.width = '360px'
      modal.style.boxSizing = 'border-box'
      modal.style.background = 'white'
      modal.style.borderRadius = '8px'
      modal.style.boxShadow =
        '0px 0px 1px rgba(24, 26, 32, 0.1), 0px 16px 32px rgba(71, 77, 87, 0.16), 0px 8px 16px rgba(24, 26, 32, 0.16)'
      modal.style.flexDirection = 'column'
      modal.style.padding = '24px'

      if (modalimg) {
        var image = document.createElement('img')
        image.src = modalimg
        image.style.height = '96px'
        image.style.width = '96px'
        image.style.margin = '5px auto'
        modal.appendChild(image)
      }

      if (modaltitle) {
        var title = document.createElement('div')
        title.style.height = '28px'
        title.style.lineHeight = '28px'
        title.style.width = '100%'
        title.style.textAlign = 'center'
        title.style.fontSize = '20px'
        title.style.fontWeight = '500'
        title.style.marginTop = '16px'
        title.style.color = '#1E2329'
        title.innerText = modaltitle
        modal.appendChild(title)
      }

      var content = document.createElement('div')
      content.style.lineHeight = '20px'
      content.style.width = '100%'
      content.style.textAlign = 'center'
      content.style.fontSize = '14px'
      content.style.margin = 'auto'
      content.style.marginTop = '8px'
      content.style.color = '#707A8A'
      content.innerHTML = modalcontent
      modal.appendChild(content)

      var bottonbox = document.createElement('div')
      bottonbox.style.marginTop = '24px'

      if (checkboxElem) {
        modal.appendChild(checkboxElem)
      }

      if (okContent) {
        var primarybutton = document.createElement('button')
        primarybutton.setAttribute('id', okBtnId || 'Modalprimarybutton-common')
        primarybutton.style.flex = '1'
        primarybutton.style.height = '40px'
        primarybutton.style.outline = 'none'
        primarybutton.style.border = 'none'
        primarybutton.style.background = '#fcd535'
        primarybutton.style.color = 'rgb(24, 26, 32)'
        primarybutton.style.borderRadius = '4px'
        primarybutton.style.display = 'flex'
        primarybutton.style.alignItems = 'center'
        primarybutton.style.justifyContent = 'center'
        primarybutton.style.width = '100%'
        primarybutton.style.fontWeight = 500
        primarybutton.style.cursor = 'pointer'
        primarybutton.innerText = okContent
        primarybutton.onclick = function () {
          clearModal()
          handleOk && handleOk()
        }

        bottonbox.appendChild(primarybutton)
      }

      if (cancelContent) {
        var cancelbutton = document.createElement('button')
        cancelbutton.setAttribute('id', cancelBtnId || 'Modalprimarybutton-common')
        cancelbutton.style.flex = '1'
        cancelbutton.style.height = '40px'
        cancelbutton.style.color = '#F0B90B'
        cancelbutton.style.outline = 'none'
        cancelbutton.style.border = 'none'
        cancelbutton.style.display = 'flex'
        cancelbutton.style.alignItems = 'center'
        cancelbutton.style.justifyContent = 'center'
        cancelbutton.style.marginRight = '8px'
        cancelbutton.style.width = '100%'
        cancelbutton.style.backgroundColor = 'white'
        cancelbutton.style.fontWeight = 500
        cancelbutton.style.cursor = 'pointer'
        cancelbutton.innerText = cancelContent
        cancelbutton.onclick = function () {
          handleCancel && handleCancel()
        }
        bottonbox.appendChild(cancelbutton)
      }

      modal.appendChild(bottonbox)

      node.appendChild(modal)
      document.body.appendChild(node)
    }

    function hkPopup() {
      var { region, locale } = bnvelidate || {}
      if (!region || region.toLowerCase() !== 'hk') return
      if (locale === 'zh-TC') return

      var OK_BTN_ID = 'binance_hk_compliance_popup_proceed'
      var CANCEL_BTN_ID = 'binance_hk_compliance_popup_cancel'
      var storageKey = 'kyc-poper' + 'hk-ip'

      getGetHeaderI18n().then(function (getHeaderI18n) {
        showModal({
          modaltitle: '',
          modalcontent: 'bn-hk-limit-popup-content',
          modalimg: warningIcon,
          cancelContent: 'bn-pop-cancel',
          cancelBtnId: CANCEL_BTN_ID,
          okContent: 'bn-pop-proceed',
          okBtnId: OK_BTN_ID,
          handleOk: function () {
            window.localStorage.setItem(storageKey, Date.now().toString())
          },
          handleCancel: function () {
            var historyLength = window.history.length
            setTimeout(function () {
              if (historyLength > 1) {
                window.history.go(-1)
              } else {
                window.close()
              }
            }, 500)
          },
          namespace: 'hk-ip',
          displayInterval: 60 * 60 * 24 * 7 * 1000, // 7 days
          afterDisplayIntervalCheck: function () {
            window.localStorage.removeItem(storageKey)
            track('ComponentShow', { $element_id: 'binance_hk_compliance_popup' })
          },
          getHeaderI18n,
        })
      })
    }

    function getTrackInstance() {
      return ['sensors', 'SensorsSdk'].reduce(function (instance, cur) {
        if (instance) return instance
        return window[cur]
      }, null)
    }

    function track(eventType, params) {
      var instance = getTrackInstance()
      if (instance) {
        instance.track(eventType, params)
        return
      }
      var elementId = params.$element_id

      if (!elementId) return
      var btn = document.createElement('button')
      btn.setAttribute('id', elementId)
      btn.style.display = 'none'
      document.body.appendChild(btn)
      btn.click()
      btn.remove()
    }
  }

  console.log('global/common deprecated version 1209')
  pollAndWaitCondition(
    main,
    function () {
      return window._EXTENSION_COMMON_MIGRATION_FLAG
    },
    200,
    5000,
    'main func in global/common.js',
  )
})()
