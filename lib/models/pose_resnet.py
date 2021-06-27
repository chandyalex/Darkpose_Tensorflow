from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
import logging
import math

import tensorflow.keras.backend as K
import tensorflow
from tensorflow import keras
from tensorflow.keras.models import Model, Sequential
from tensorflow.keras.layers import Input, Conv2D, BatchNormalization, Activation
from tensorflow.keras.layers import MaxPooling2D, Conv2DTranspose, Input,Flatten,ReLU
from tensorflow.keras.layers import UpSampling2D, add, concatenate,AveragePooling2D,MaxPooling2D

BN_MOMENTUM = 0.1
logger = logging.getLogger(__name__)




def conv3x3(input, out_filters, strides=(1, 1)):

    x = Conv2D(filters=out_filters, kernel_size=(3,3), strides=strides, use_bias=False,padding='same')
    return x


class basic_Block(tensorflow.keras.Model):
    expansion = 1
    def __init__(self,inplanes, planes, strides=(1, 1), with_downsample=None):
        super(basic_Block, self).__init__()

        self.conv1 = conv3x3(input=inplanes, out_filters=planes, strides=strides)
        self.bn1 = BatchNormalization(momentum=BN_MOMENTUM)
        self.relu = ReLU()
        self.conv2 = conv3x3(input=planes, out_filters=planes,strides=strides)
        self.bn2 = BatchNormalization(momentum=BN_MOMENTUM)
        self.downsample = with_downsample
        self.stride = strides
        self.planes=planes

    def call(self, x):

        residual=x
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)

        x = self.conv2(x)
        x = self.bn2(x)

        if self.downsample is not None:
            residual = self.downsample(residual)
        x = keras.layers.add([x,residual])

        x = self.relu(x)
        return x


class bottleneck_Block(tensorflow.keras.Model):
    expansion = 4
    def __init__(self,inplanes, planes, strides=(1, 1), with_downsample=None):
        super(bottleneck_Block, self).__init__()


        self.conv1 = Conv2D(filters=planes, kernel_size=(1,1),
                                            use_bias=False,padding='same')
        self.bn1 = BatchNormalization(axis=3,momentum=BN_MOMENTUM)
        self.conv2 = Conv2D(filters=planes, kernel_size=(3,3), strides=strides,
                               use_bias=False,padding='same')
        self.bn2 = BatchNormalization(axis=3,momentum=BN_MOMENTUM)
        self.conv3 = Conv2D(filters=planes * self.expansion, kernel_size=(1,1),
                               use_bias=False,padding='same')
        self.bn3 = BatchNormalization(axis=3,momentum=BN_MOMENTUM)
        self.relu = ReLU()
        self.downsample = with_downsample
        self.stride = strides
        self.planes=planes

    def call(self, x):
        padding=keras.layers.ZeroPadding2D(padding=(1,1))
        residual = x

        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.conv2(x)
        x = self.bn2(x)
        x = self.relu(x)

        x = self.conv3(x)
        x = self.bn3(x)

        if self.downsample is not None:
            residual = self.downsample(residual)

        x = keras.layers.add([x,residual])

        x = self.relu(x)


        return x


class PoseResNet(tensorflow.keras.Model):
    def __init__(self, block, layers, cfg, **kwargs):

        self.inplanes = 64
        extra = cfg.MODEL.EXTRA
        self.deconv_with_bias = extra.DECONV_WITH_BIAS

        super(PoseResNet, self).__init__()


        self.padding1=keras.layers.ZeroPadding2D(padding=(3,3))


        self.conv1 = Conv2D(filters=64, kernel_size=(7,7),
                                strides=(2,2),use_bias=True,padding="same")


        self.bn1 = BatchNormalization(momentum=BN_MOMENTUM)
        self.relu = ReLU()
        self.padding2=keras.layers.ZeroPadding2D(padding=(1,1))
        self.maxpool = MaxPooling2D(pool_size=(3,3), strides=(2,2),padding="same")


        self.layer1 = self._make_layer(block, 64, layers[0],stride=1)
        self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
        self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
        self.layer4 = self._make_layer(block, 512, layers[3], stride=2)

        # used for deconv layers
        self.deconv_layers = self._make_deconv_layer(
            extra.NUM_DECONV_LAYERS,
            extra.NUM_DECONV_FILTERS,
            extra.NUM_DECONV_KERNELS,
        )

        self.final_layer = Conv2D(
            input_shape=(extra.NUM_DECONV_FILTERS[-1],),
            filters=cfg.MODEL.NUM_JOINTS,
            kernel_size=extra.FINAL_CONV_KERNEL,
            strides=(1,1),padding="same")

    def _make_layer(self, block, planes, blocks, stride=1):

        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            # downsample=True
            downsample = Sequential([
                Conv2D(filters = planes * block.expansion,
                          kernel_size=(1,1), strides=stride,use_bias=False,padding="same"),
                BatchNormalization(momentum=BN_MOMENTUM)])

        layers = []

        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for i in range(1, blocks):

            layers.append(block(self.inplanes, planes))
        return Sequential(layers)

    def _get_deconv_cfg(self, deconv_kernel, index):
        if deconv_kernel == 4:
            padding = 1
            output_padding = 0
        elif deconv_kernel == 3:
            padding = 1
            output_padding = 1
        elif deconv_kernel == 2:
            padding = 0
            output_padding = 0

        return deconv_kernel, padding, output_padding

    def _make_deconv_layer(self, num_layers, num_filters, num_kernels):
        assert num_layers == len(num_filters), \
            'ERROR: num_deconv_layers is different len(num_deconv_filters)'
        assert num_layers == len(num_kernels), \
            'ERROR: num_deconv_layers is different len(num_deconv_filters)'

        layers = []
        layers_new=[]
        for i in range(num_layers):
            kernel, padding, output_padding = \
                self._get_deconv_cfg(num_kernels[i], i)

            planes = num_filters[i]

            layers.append(keras.layers.ZeroPadding2D(padding=(padding,padding)))
            layers.append(
                Conv2DTranspose(
                    input_shape=(self.inplanes,self.inplanes,3),
                    filters=planes,
                    kernel_size=kernel,
                    strides=(2,2),
                    use_bias=self.deconv_with_bias))

            layers.append(BatchNormalization(momentum=BN_MOMENTUM))
            layers.append(ReLU())
            self.inplanes = planes


        return Sequential(layers)


    def call(self, x):
        x= self.padding1(x)
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x= self.padding2(x)
        x = self.maxpool(x)
        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.deconv_layers(x)
        x = self.padding2(x)
        x = self.final_layer(x)

        return x

    def init_weights(self, pretrained=''):
        if os.path.isfile(pretrained):
            logger.info('=> init deconv weights from normal distribution')
            for name, m in self.deconv_layers.named_modules():
                if isinstance(m, nn.ConvTranspose2d):
                    logger.info('=> init {}.weight as normal(0, 0.001)'.format(name))
                    logger.info('=> init {}.bias as 0'.format(name))
                    nn.init.normal_(m.weight, std=0.001)
                    if self.deconv_with_bias:
                        nn.init.constant_(m.bias, 0)
                elif isinstance(m, nn.BatchNorm2d):
                    logger.info('=> init {}.weight as 1'.format(name))
                    logger.info('=> init {}.bias as 0'.format(name))
                    nn.init.constant_(m.weight, 1)
                    nn.init.constant_(m.bias, 0)
            logger.info('=> init final conv weights from normal distribution')
            for m in self.final_layer.modules():
                if isinstance(m, nn.Conv2d):
                    # nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                    logger.info('=> init {}.weight as normal(0, 0.001)'.format(name))
                    logger.info('=> init {}.bias as 0'.format(name))
                    nn.init.normal_(m.weight, std=0.001)
                    nn.init.constant_(m.bias, 0)

            pretrained_state_dict = torch.load(pretrained)
            logger.info('=> loading pretrained model {}'.format(pretrained))
            self.load_state_dict(pretrained_state_dict, strict=False)
        else:
            logger.info('=> init weights from normal distribution')
            for m in self.modules():
                if isinstance(m, nn.Conv2d):
                    # nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
                    nn.init.normal_(m.weight, std=0.001)
                    # nn.init.constant_(m.bias, 0)
                elif isinstance(m, nn.BatchNorm2d):
                    nn.init.constant_(m.weight, 1)
                    nn.init.constant_(m.bias, 0)
                elif isinstance(m, nn.ConvTranspose2d):
                    nn.init.normal_(m.weight, std=0.001)
                    if self.deconv_with_bias:
                        nn.init.constant_(m.bias, 0)
resnet_spec = {
    18: (basic_Block, [2, 2, 2, 2]),
    34: (basic_Block, [3, 4, 6, 3]),
    50: (bottleneck_Block, [3, 4, 6, 3]),
    101: (bottleneck_Block, [3, 4, 23, 3]),
    152: (bottleneck_Block, [3, 8, 36, 3])
}

def get_pose_net(cfg, is_train, **kwargs):
    num_layers = cfg.MODEL.EXTRA.NUM_LAYERS

    block_class, layers = resnet_spec[num_layers]

    model = PoseResNet(block_class, layers, cfg, **kwargs)

    if is_train and cfg.MODEL.INIT_WEIGHTS:
        model.init_weights(cfg.MODEL.PRETRAINED)

    return model
