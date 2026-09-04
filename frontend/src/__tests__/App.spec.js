import { describe, it, expect } from 'vitest'

import { shallowMount } from '@vue/test-utils'
import App from '../App.vue'

// App needs Pinia for the router/Sidebar tree; shallow-mount it via a small
// wrapper so the test stays focused on "it boots without throwing".
describe('App', () => {
  it('mounts and renders the layout shell', () => {
    const wrapper = shallowMount(App, {
      global: {
        stubs: { RouterView: true, Sidebar: true },
      },
    })
    expect(wrapper.exists()).toBe(true)
    expect(wrapper.get('main').exists()).toBe(true)
  })
})

