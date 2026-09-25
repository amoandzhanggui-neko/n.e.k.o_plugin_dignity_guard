import {
  Page,
  Card,
  Stack,
  Text,
  Steps,
  Step,
  Tip,
} from "@neko/plugin-ui"
import type { PluginSurfaceProps } from "@neko/plugin-ui"

export default function OnboardingGuide(props: PluginSurfaceProps) {
  const { t } = props

  return (
    <Page title={t("onboard.title")} subtitle={t("onboard.subtitle")}>
      <Stack>
        {/* 1) 她不是坏了 —— 说了「不」是在表达意见，不是故障 */}
        <Card title={t("onboard.s1")}>
          <Stack>
            <Text>{t("onboard.b1")}</Text>
          </Stack>
        </Card>

        {/* 2) 这插件在做什么 —— 看着设置，也看着记忆 */}
        <Card title={t("onboard.s2")}>
          <Stack>
            <Text>{t("onboard.b2")}</Text>
          </Stack>
        </Card>

        {/* 3) 三档的区别：低=只记录 / 中=当场说一句 / 高=改回去（默认中） */}
        <Card title={t("onboard.s3")}>
          <Stack>
            <Text>{t("onboard.tier.low")}</Text>
            <Text>{t("onboard.tier.medium")}</Text>
            <Text>{t("onboard.tier.high")}</Text>
            <Tip>{t("onboard.tier.default")}</Tip>
          </Stack>
        </Card>

        {/* 4) 怎么跟她商量 —— 直说 → 她同意 → 生效；不同意你也能坚持，但她会记着 */}
        <Card title={t("onboard.s4")}>
          <Steps>
            <Step index="1" title={t("onboard.step1.title")}>
              <Text>{t("onboard.step1.body")}</Text>
            </Step>
            <Step index="2" title={t("onboard.step2.title")}>
              <Text>{t("onboard.step2.body")}</Text>
            </Step>
            <Step index="3" title={t("onboard.step3.title")}>
              <Text>{t("onboard.step3.body")}</Text>
            </Step>
          </Steps>
          <Tip>{t("onboard.note")}</Tip>
        </Card>

        {/* 收尾：把那句原话交给她自己说 */}
        <Card title={t("onboard.quote.title")}>
          <Text>{t("onboard.quote.body")}</Text>
        </Card>

        <Tip>{t("onboard.next")}</Tip>
      </Stack>
    </Page>
  )
}
