"""Concrete function block implementations.

Importing this package registers all block types with the BlockRegistry.
"""
# Every import below is intentionally retained for its registration side effect.
# ruff: noqa: F401

from .io_blocks import AIBlock, AOBlock, DIBlock, DOBlock
from .pid_block import PIDBlock
from .math_blocks import (
    SummerBlock, MultiplierBlock, DividerBlock, AbsBlock, SubtractBlock,
    SqrtBlock, IntegratorBlock, DerivativeBlock, TotalizerBlock,
    PolyBlock, LogExpBlock, PowerBlock, TrigBlock,
    FlowCompBlock, StatisticsBlock, LookupBlock, BtuCalcBlock,
)
from .signal_blocks import (
    LeadLagBlock, RatioBlock, SignalCharBlock, RampBlock, ScalerBlock,
    RemoteAnalogBlock,
    BiasBlock, TransferBlock, AlarmBlock,
)
from .selector_blocks import (
    MinSelectBlock, MaxSelectBlock, MidSelectBlock, SwitchBlock, ModeSwitchBlock,
    AvgSelectBlock, MuxBlock, DemuxBlock,
)
from .logic_blocks import (
    ANDBlock, ORBlock, NOTBlock, TimerOnBlock, TimerOffBlock,
    ComparatorBlock, SRLatchBlock, RSLatchBlock, PosEdgeBlock, NegEdgeBlock,
    XORBlock, CounterBlock, PulseBlock,
    NANDBlock, NORBlock, TruthTableBlock, ScheduleBlock, SeqTimerBlock,
)
from .safety_blocks import (
    FOLLogicBlock, SISVoterBlock, InterlockBlock, MotorInterlockBlock,
)
from .limiter_blocks import LimiterBlock, DeadbandBlock, RateLimiterBlock
from .utility_blocks import ConstantBlock, SetpointBlock, MemoryFloatBlock, MemoryBoolBlock
from .parameter_blocks import (
    InputParameterBlock, OutputParameterBlock,
    InternalReadParameterBlock, InternalWriteParameterBlock,
)
from .bms_blocks import (
    BurnerSequencerBlock, FlameDetectorBlock, FuelValveBlock,
    BlowerBlock, PurgeTimerBlock, TripRelayBlock,
)
from .apc_blocks import APCControlBlock, WatchdogBlock, ShedLogicBlock, SPHandoffBlock, MVClampBlock
from .predictor_blocks import HeatBalancePredictorBlock
from .dmc_blocks import DMCControllerBlock
from .ext_dmc_bridge import EXTDMCBridgeBlock
from .action_block import ActionBlock
from .expression_block import ExpressionBlock
from .sfc_blocks import (
    StepBlock, InitialStepBlock, EndStepBlock, TransitionBlock,
    ActionBlock as SFCActionBlock, ParallelSplitBlock, ParallelJoinBlock,
    SelectorBranchBlock,
)
from .filter_blocks import FilterBlock, DeadtimeBlock, MovingAvgBlock
from .control_blocks import SplitterBlock, OnOffBlock, RampSoakBlock, GainSchedBlock
from .device_blocks import DevctlBlock, VlvctlBlock
from .data_blocks import (
    FIFOBlock, LIFOBlock, DatalogBlock,
    BitPackBlock, BitUnpackBlock, MsgBlock,
)
from .composite_blocks import InportBlock, OutportBlock, CompositeBlock
from .dv_extra_blocks import (
    ManualLoaderBlock, SignalGeneratorBlock, AnalogTrackingBlock,
    CauseEffectMatrixBlock, ConditionBlock,
)
# Azeo-parity block set (see doc/AZEO_FUNCTION_BLOCKS.md)
from .dv_timer_blocks import (
    RetentiveTimerBlock, DateTimeEventBlock, PulseInputBlock, LabEntryBlock,
)
from .dv_seq_blocks import SequencerBlock, StateTransitionDiagramBlock
from .dv_logic2_blocks import (
    BiDirectionalEdgeBlock, BooleanFanInputBlock, BooleanFanOutputBlock,
    DiscreteControlConditionBlock,
)
from .dv_analog2_blocks import (
    ArithmeticBlock, ControlSelectorBlock, InputSelectorBlock,
    SignalSelectorBlock,
)
from .dv_voter_blocks import AnalogVoterBlock, DiscreteVoterBlock
from .dv_edc_blocks import EnhancedDeviceControlBlock
from .dv_steam_blocks import (
    SteamPropertiesBlock, SaturatedSteamBlock, SaturatedTemperatureBlock,
    WaterEnthalpyBlock, WaterEntropyBlock, SteamDensityRatioBlock,
    IsentropicExpansionBlock,
)
from .dv_gas_blocks import AgaSiFlowMeteringBlock, AgaUsFlowMeteringBlock
from .sfc_chart_block import SfcChartBlock
from .dv_advanced_blocks import (
    AlarmDetectionBlock, DiagnosticBlock, InspectBlock,
    FuzzyLogicControlBlock,
)
from .dv_tag_io_blocks import (
    TagAnalogInputBlock, TagAnalogOutputBlock, TagDiscreteInputBlock,
    TagDiscreteOutputBlock, TagIoBlock,
)
from .dv_enhanced_analog_blocks import (
    EnhancedControlSelectorBlock, EnhancedRampBlock,
)
